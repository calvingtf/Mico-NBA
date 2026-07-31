"""Run the validator against trades that actually happened.

    python -m mironba.eval.real_trades --season 2024-25

M0's charter said "test against ~30 real trades". What was built instead was a
coverage matrix of synthetic fixtures on rule boundaries, and
``tests/test_real_trades.py`` says so in its own docstring. The reasoning was
sound — every rule path wants a fixture, and a real trade exercises whichever
paths it happens to touch — but it means the validator has never been pointed
at the transaction log.

This points it there. Every two-team trade with named players from a season's
log is reconstructed and validated, and the result is reported as three
buckets: approved, undetermined, and rejected. **A rejection is a finding about
the validator, not about the league.** These trades were made; the league
allowed them. If the validator says no, either a rule is wrong or an input is.

## The input that is wrong, stated up front

Team salary *on the trade date* is not available. The ingest has season cap
hits, so payroll is summed across a whole season's contracts — which counts
players who arrived later, misses players who left, and ignores dead money and
cap holds. This is exactly the REALITY row the M0 coverage matrix deferred, and
it is still deferred; what has changed is that the consequence is now
measurable rather than hypothetical.

Expect that proxy to reject legal trades: a team's summed-season payroll runs
above its true in-season figure, which pushes it into a higher apron tier and
tightens its matching limit. The direction of the error is knowable even where
the size is not, and it is reported per trade so the pattern is visible.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mironba.data.candidates import TEAM_NAMES
from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    PickAsset,
    PlayerAsset,
    ReSignStatus,
    Severity,
    TeamTradeState,
    Trade,
    Verdict,
    validate_trade,
)

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

#: Roster size on a trade date is not available; the contracts table is a
#: season total. 14 is used because it is one below the limit and therefore
#: cannot invent a roster violation, and roster findings are excluded from the
#: legality rate rather than counted against the validator.
ROSTER_UNKNOWN = 14


def _service_years(season: str) -> dict[str, int]:
    """Years of NBA service per Basketball-Reference id, as of ``season``.

    From ``careers.csv`` (stats.nba.com FROM_YEAR), matched to contract ids by
    normalised name. This is what the minimum-salary scale keys on, and nothing
    in the Basketball-Reference ingest carries it. Without it every player fell
    to the zero-experience minimum, which refuses the minimum-salary exception
    to players who qualify and rejects trades the league allowed.
    """
    import unicodedata

    careers = SNAPSHOTS / "nba-stats" / "careers.csv"
    if not careers.is_file():
        return {}

    def norm(name: str) -> str:
        text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z]", "", text.lower())

    from_year: dict[str, int] = {}
    with careers.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                from_year[norm(row["DISPLAY_FIRST_LAST"])] = int(row["FROM_YEAR"])
            except (TypeError, ValueError):
                continue

    start = int(season[:4])
    out: dict[str, int] = {}
    players = SNAPSHOTS / f"bbref-{season}" / "players.csv"
    if not players.is_file():
        return {}
    with players.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            debut = from_year.get(norm(row["name"]))
            if debut is None:
                continue
            out[row["player_id"]] = max(0, start - debut)
    return out


def _season_ids(season: str) -> set[str]:
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["player_id"] for r in csv.DictReader(handle)}


def _draft_rights(season: str) -> set[str]:
    """Players whose rights, not contracts, were being traded.

    A player with no contract in the season being priced but one in an
    adjacent season had rights rather than salary at the time. Matching does
    not apply to him, so validating such a trade measures a category error
    rather than the validator - both first-run rejections were draft-night
    deals of exactly this kind.

    Checked in both directions because the transaction log for a season runs
    from the previous June to the following June, so it contains draft nights
    at both ends: incoming rookies at the start and the next class at the end.
    """
    start = int(season[:4])
    previous = f"{start - 1}-{str(start % 100).zfill(2)}"
    following = f"{start + 1}-{str((start + 2) % 100).zfill(2)}"
    now = _season_ids(season)
    return (
        (now - _season_ids(previous))
        | (_season_ids(following) - now)
    )

#: "The <A> traded <out> to the <B> for <in>." Both halves carry {{id}} marks.
_TWO_TEAM = re.compile(
    r"^The (?P<a>.+?) traded (?P<out>.+?) to the (?P<b>.+?) for (?P<back>.+?)\.?$"
)
_MARK = re.compile(r"\{\{(\w+)\}\}")
#: Draft picks carry the eventual selection in parentheses - "a 2025 1st round
#: draft pick (Walter Clayton Jr.{{claytwa01}} was later selected)". Those
#: players were not in the trade; they did not exist as NBA contracts yet. Left
#: in, they made 15 of 19 trades unscoreable for the entirely wrong reason.
_PARENTHETICAL = re.compile(r"\([^()]*\)")


def _traded_ids(fragment: str) -> tuple[str, ...]:
    return tuple(_MARK.findall(_PARENTHETICAL.sub(" ", fragment)))


@dataclass(frozen=True, slots=True)
class Move:
    """One player changing hands. Multi-team trades are not symmetric, so a
    destination has to be carried per player rather than per side."""

    player_id: str
    from_team: str
    to_team: str


@dataclass(frozen=True, slots=True)
class PickMove:
    from_team: str
    to_team: str
    draft_year: int
    round: int
    #: Text as written - "top 4 protected", "is a swap". Parsed into a
    #: structured protection by ``rules/picks.py``; kept verbatim here so the
    #: parser never has to interpret.
    note: str = ""


@dataclass
class RealTrade:
    when: date
    season: str
    teams: tuple[str, ...]
    moves: tuple[Move, ...]
    text: str
    picks: tuple[PickMove, ...] = ()

    @property
    def n_teams(self) -> int:
        return len(self.teams)

    def sends(self, team: str) -> tuple[str, ...]:
        return tuple(m.player_id for m in self.moves if m.from_team == team)

    def receives(self, team: str) -> tuple[str, ...]:
        return tuple(m.player_id for m in self.moves if m.to_team == team)

    # Two-team accessors, kept because the deadline scorer and several tests
    # were written against them before multi-team parsing existed.
    @property
    def team_a(self) -> str:
        return self.teams[0]

    @property
    def team_b(self) -> str:
        return self.teams[1]

    @property
    def a_sends(self) -> tuple[str, ...]:
        return self.sends(self.teams[0])

    @property
    def b_sends(self) -> tuple[str, ...]:
        return self.sends(self.teams[1])

    @property
    def representable(self) -> bool:
        """Every participant sends at least one named player.

        Under two-team parsing this meant "both sides send a player", which
        excluded players-for-picks because picks had no value. With a pick
        model that reason is gone for the *pricing*, but the criterion stays
        for a different one: a team that sends nothing is a pure absorber, and
        its side of the salary match is trivially satisfied. Those are counted
        separately rather than folded into the legality rate.
        """
        return all(self.sends(t) for t in self.teams)

    @property
    def sends_only_picks(self) -> tuple[str, ...]:
        """Participants sending no players. Named, not silently dropped."""
        return tuple(t for t in self.teams if not self.sends(t))


#: One leg of a multi-team trade: "the X traded A, B and a pick to the Y".
#: Basketball-Reference writes every N-team deal as a semicolon-separated list
#: of these, which is why a targeted parser beats trying to generalise the
#: two-team "traded ... for ..." form.
_LEG = re.compile(
    r"the (?P<from>[A-Z][^;]*?) traded (?P<what>.+?) to the (?P<to>[A-Z][^;]*?)\s*$"
)
_HEADER = re.compile(r"^In a (?P<n>\d+)-team trade,\s*")
_PICK = re.compile(
    r"(?P<year>\d{4}) (?P<round>1st|2nd) round draft pick"
)


def _pick_moves(fragment: str, src: str, dst: str) -> tuple[PickMove, ...]:
    """Picks named in one leg. The parenthetical says who was *later* selected
    and is stripped: that player was not in the trade and did not exist as an
    NBA contract at the time."""
    clean = _PARENTHETICAL.sub(" ", fragment)
    return tuple(
        PickMove(src, dst, int(m.group("year")), 1 if m.group("round") == "1st" else 2)
        for m in _PICK.finditer(clean)
    )


def parse_trades(season: str, max_teams: int = 3) -> list[RealTrade]:
    """Every trade in a season's log with named players, up to ``max_teams``.

    Supersedes ``parse_two_team_trades``, which is kept as a filter over this.
    The two-team restriction was the single largest cause of the empty
    denominator: real deadline business is multi-team, and of 19 trade rows in
    the 2025 deadline window only one was a two-team deal with players moving
    both ways.

    ``max_teams`` is 3 by charter. Four- and five-team deals parse identically
    and are counted as out of scope rather than mis-parsed, because the solver
    is only being extended to three.
    """
    path = SNAPSHOTS / f"bbref-{season}" / "transactions.csv"
    if not path.is_file():
        return []
    out: list[RealTrade] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["is_trade"] != "1" or not row["player_ids"].strip():
                continue
            teams = tuple(row["team_ids"].split("|"))
            if not 2 <= len(teams) <= max_teams:
                continue
            parsed = _parse_row(row, season, teams)
            if parsed is not None:
                out.append(parsed)
    return out


def _parse_row(row: dict, season: str, teams: tuple[str, ...]) -> RealTrade | None:
    text = row["marked_text"].strip().rstrip(".").strip()
    when = date.fromisoformat(row["date"])

    header = _HEADER.match(text)
    if header is None:
        # Two-team form: "The A traded X to the B for Y."
        match = _TWO_TEAM.match(row["marked_text"].strip())
        if match is None or len(teams) != 2:
            return None
        a, b = teams
        moves = tuple(
            Move(pid, a, b) for pid in _traded_ids(match.group("out"))
        ) + tuple(
            Move(pid, b, a) for pid in _traded_ids(match.group("back"))
        )
        picks = _pick_moves(match.group("out"), a, b) + _pick_moves(
            match.group("back"), b, a
        )
        return RealTrade(when, season, teams, moves, row["text"], picks)

    # N-team form. Trailing prose after the final leg ("Atlanta received a
    # trade exception", pick conditions) is not part of any leg, so each leg is
    # matched from its right edge and unmatched tails are dropped rather than
    # guessed at.
    body = text[header.end():]
    moves: list[Move] = []
    picks: list[PickMove] = []
    seen: set[str] = set()
    for clause in body.split(";"):
        clause = clause.strip().removeprefix("and ").strip()
        leg = _LEG.match(clause)
        if leg is None:
            continue
        src = TEAM_NAMES.get(leg.group("from").strip())
        dst = TEAM_NAMES.get(leg.group("to").strip().split(" . ")[0].strip())
        if src is None or dst is None or src == dst:
            continue
        seen.update((src, dst))
        moves.extend(Move(pid, src, dst) for pid in _traded_ids(leg.group("what")))
        picks.extend(_pick_moves(leg.group("what"), src, dst))

    if not moves or not seen.issubset(set(teams)):
        return None
    return RealTrade(when, season, teams, tuple(moves), row["text"], tuple(picks))


def parse_two_team_trades(season: str) -> list[RealTrade]:
    """Two-team trades only. A filter over :func:`parse_trades`, kept so the
    pre-M9 coverage figures stay recomputable and comparable."""
    return [t for t in parse_trades(season, max_teams=2) if t.n_teams == 2]


def _contracts(season: str) -> tuple[dict[str, int], dict[str, str], dict[str, int]]:
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    salary: dict[str, int] = {}
    team: dict[str, str] = {}
    payroll: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            salary[row["player_id"]] = int(row["salary"])
            team[row["player_id"]] = row["team_id"]
            payroll[row["team_id"]] = payroll.get(row["team_id"], 0) + int(row["salary"])
    return salary, team, payroll


@dataclass
class TradeCheck:
    trade: RealTrade
    verdict: Verdict | None
    errors: tuple[str, ...]
    missing: tuple[str, ...]
    skip_reason: str = "no salary"

    @property
    def scored(self) -> bool:
        return self.verdict is not None

    @property
    def legal(self) -> bool:
        return self.verdict in (Verdict.APPROVED, Verdict.UNDETERMINED)

    @property
    def roster_only(self) -> bool:
        """Rejected solely on roster count, which the ingest does not carry.

        Kept separate rather than folded into the legality rate: a season
        contracts table counts everyone who appeared, so roster size on the
        trade date is unavailable, and a rejection that turns on it measures
        the input rather than the rule.
        """
        return bool(self.errors) and all("ROSTER_LIMIT" in e for e in self.errors)

    def line(self, names=lambda p: p) -> str:
        teams = "/".join(self.trade.teams)
        if not self.scored:
            return (
                f"  SKIP {self.trade.when} {teams}"
                f"  {self.skip_reason}: {', '.join(self.missing[:3])}"
            )
        mark = {"approved": "OK  ", "undetermined": "?   "}.get(
            self.verdict.value, "NO  "
        )
        detail = f"  {self.errors[0][:88]}" if self.errors else ""
        shape = "-".join(str(len(self.trade.sends(t))) for t in self.trade.teams)
        return f"  {mark}{self.trade.when} {teams}  sends {shape}{detail}"


def check(trade: RealTrade) -> TradeCheck:
    """Validate one real trade, two teams or three.

    The validator has always been N-team - ``Trade.teams`` is a tuple and each
    participant is checked against its own cap position - but this harness was
    written when the parser could only produce two-team deals, so it hardcoded
    ``team_a``/``team_b``. That, not the rules, was what capped the scoreable
    denominator at 4 across three deadlines.
    """
    salary, team_of, payroll = _contracts(trade.season)
    env = environment_for(trade.season)

    everyone = tuple(m.player_id for m in trade.moves)
    missing = tuple(p for p in everyone if p not in salary)
    if missing:
        return TradeCheck(trade, None, (), missing, skip_reason="no salary")

    # Draft-rights trades are not contract trades: a player with no contract
    # in the season being priced has rights rather than salary, and matching
    # does not apply to him. Both first-run rejections were draft-night deals
    # of exactly this kind.
    rights = _draft_rights(trade.season) & set(everyone)
    if rights:
        return TradeCheck(
            trade, None, (), tuple(sorted(rights)), skip_reason="draft rights",
        )

    # Roster count on the trade date is not in the ingest either - the
    # contracts table is a season total and counts everyone who appeared.
    # Every participant is given 14, one below the limit, which is the only
    # value that cannot manufacture a roster finding out of an input we do not
    # have. Roster-limit rejections are reported separately for the same reason.
    states = tuple(
        TeamTradeState(team, payroll.get(team, 0), ROSTER_UNKNOWN)
        for team in trade.teams
    )
    service = _service_years(trade.season)
    players = tuple(
        PlayerAsset(
            player_id=move.player_id, name=move.player_id,
            salary=salary[move.player_id],
            from_team=move.from_team, to_team=move.to_team,
            re_sign_status=ReSignStatus.UNKNOWN,
            years_of_service=service.get(move.player_id),
        )
        for move in trade.moves
    )
    # Picks are carried through so the Stepien check sees them. Their *value*
    # is not modelled; what matters here is that a first-rounder leaving a team
    # is visible to the rule that cares.
    picks = tuple(
        PickAsset(
            from_team=pick.from_team, to_team=pick.to_team,
            draft_year=pick.draft_year, round=pick.round,
        )
        for pick in trade.picks
    )
    result = validate_trade(
        Trade(season=trade.season, trade_date=trade.when, teams=states,
              players=players, picks=picks),
        env,
    )
    errors = tuple(
        str(f) for f in result.findings if f.severity is Severity.ERROR
    )
    return TradeCheck(trade, result.verdict, errors, ())


def main(argv=None) -> int:
    from mironba.sim.tick import use_utf8_console

    use_utf8_console()
    parser = argparse.ArgumentParser(description="Validate real trades.")
    parser.add_argument("--season", default="2024-25")
    args = parser.parse_args(argv)

    trades = [t for t in parse_two_team_trades(args.season) if t.representable]
    checks = [check(t) for t in trades]
    scored = [c for c in checks if c.scored]

    print("=" * 74)
    print(f"  validator against real {args.season} trades")
    print("=" * 74)
    parsed = parse_two_team_trades(args.season)
    print(f"  {len(parsed)} two-team trades parsed, {len(trades)} with players "
          f"on both sides, {len(scored)} with salaries for everyone")
    print()
    for c in checks:
        print(c.line())

    roster_only = [
        c for c in scored
        if not c.legal and all("ROSTER" in e for e in c.errors)
    ]
    scored = [c for c in scored if c not in roster_only]
    approved = sum(1 for c in scored if c.verdict is Verdict.APPROVED)
    undetermined = sum(1 for c in scored if c.verdict is Verdict.UNDETERMINED)
    rejected = sum(1 for c in scored if not c.legal)
    print()
    print(f"  approved      {approved}/{len(scored)}")
    print(f"  undetermined  {undetermined}/{len(scored)}   "
          f"(BYC unknowable from this ingest)")
    print(f"  REJECTED      {rejected}/{len(scored)}   "
          f"<- each one is a finding about the validator or its inputs")
    if scored:
        print(f"\n  legality hit rate: {(approved + undetermined) / len(scored):.1%}")

    if rejected:
        print("\n  why they were rejected:")
        seen: dict[str, int] = {}
        for c in scored:
            if c.legal or not c.errors:
                continue
            rule = c.errors[0].split(":")[0]
            seen[rule] = seen.get(rule, 0) + 1
        for rule, count in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"    {rule}  x{count}")
        print("\n  Team salary on the trade date is not in the ingest; these use")
        print("  summed season cap hits, which run high and tighten every")
        print("  matching limit. That is the M0 REALITY row, still deferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
