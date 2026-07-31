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

from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
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


@dataclass
class RealTrade:
    when: date
    season: str
    team_a: str
    team_b: str
    a_sends: tuple[str, ...]
    b_sends: tuple[str, ...]
    text: str

    @property
    def representable(self) -> bool:
        """Both sides send at least one named player.

        A trade that is players-for-picks is legal under rules this module does
        not model (picks have no salary), so validating it would measure the
        gap rather than the validator.
        """
        return bool(self.a_sends) and bool(self.b_sends)


def parse_two_team_trades(season: str) -> list[RealTrade]:
    path = SNAPSHOTS / f"bbref-{season}" / "transactions.csv"
    if not path.is_file():
        return []
    out: list[RealTrade] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["is_trade"] != "1" or not row["player_ids"].strip():
                continue
            teams = row["team_ids"].split("|")
            if len(teams) != 2:
                continue
            match = _TWO_TEAM.match(row["marked_text"].strip())
            if not match:
                continue
            out.append(
                RealTrade(
                    when=date.fromisoformat(row["date"]),
                    season=season,
                    team_a=teams[0],
                    team_b=teams[1],
                    a_sends=_traded_ids(match.group("out")),
                    b_sends=_traded_ids(match.group("back")),
                    text=row["text"],
                )
            )
    return out


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

    def line(self, names=lambda p: p) -> str:
        if not self.scored:
            return (
                f"  SKIP {self.trade.when} {self.trade.team_a}/{self.trade.team_b}"
                f"  {self.skip_reason}: {', '.join(self.missing[:3])}"
            )
        mark = {"approved": "OK  ", "undetermined": "?   "}.get(
            self.verdict.value, "NO  "
        )
        detail = f"  {self.errors[0][:88]}" if self.errors else ""
        return (
            f"  {mark}{self.trade.when} {self.trade.team_a}/{self.trade.team_b}"
            f"  {len(self.trade.a_sends)}-for-{len(self.trade.b_sends)}{detail}"
        )


def check(trade: RealTrade) -> TradeCheck:
    salary, team_of, payroll = _contracts(trade.season)
    env = environment_for(trade.season)

    everyone = trade.a_sends + trade.b_sends
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
    # contracts table is a season total and counts everyone who appeared. Both
    # sides are given 14, one below the limit, which is the only value that
    # cannot manufacture a roster finding out of an input we do not have.
    # Roster-limit rejections are reported separately for the same reason.
    a = TeamTradeState(trade.team_a, payroll.get(trade.team_a, 0), ROSTER_UNKNOWN)
    b = TeamTradeState(trade.team_b, payroll.get(trade.team_b, 0), ROSTER_UNKNOWN)
    service = _service_years(trade.season)
    players = tuple(
        PlayerAsset(
            player_id=pid, name=pid, salary=salary[pid],
            from_team=trade.team_a, to_team=trade.team_b,
            re_sign_status=ReSignStatus.UNKNOWN,
            years_of_service=service.get(pid),
        )
        for pid in trade.a_sends
    ) + tuple(
        PlayerAsset(
            player_id=pid, name=pid, salary=salary[pid],
            from_team=trade.team_b, to_team=trade.team_a,
            re_sign_status=ReSignStatus.UNKNOWN,
            years_of_service=service.get(pid),
        )
        for pid in trade.b_sends
    )
    result = validate_trade(
        Trade(season=trade.season, trade_date=trade.when, teams=(a, b),
              players=players),
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
