"""The 2025 trade deadline, frozen the day before.

    python -m mironba.sim.deadline

The first scenario in this project where the trade solver is scored
*predictively*. Everything before it either validated a package after the fact
or measured whether a model could state a satisfiable intent. This asks the
harder question: given the league on 2025-02-05, does the simulation propose
the trades that were made the next day?

The answer is mostly no, and the interesting content is in which parts fail.

## Why the deadline and not some other date

It is the one in-season moment where the whole league acts at once, so a single
freeze produces a lot of scoreable events. It also has a hard boundary — after
3pm ET on 2025-02-06 nothing more can happen — which means the ground truth is
closed rather than trailing off.

## The freeze

2025-02-05, the day before. Standings come from dated game results, so a team's
record is a filter rather than an end-of-season figure the simulation is partly
supposed to predict. `models/disposition.py` turns that into buyer, seller or
ambiguous, and at this freeze it returns 12 buyers, 6 sellers and 12 ambiguous.

It used to return 4/3/23, because the bands were set from the value model's
win-delta error — a projection's spread applied to an observed standing. The
bands are now measured from 90 team-seasons, and ambiguous teams act on both
sides rather than standing pat.

## What is NOT scored

The counterfactual has no branch here: this is not a decision fork, it is a
market. So everything is falsifiable and everything is scored. What is excluded
is anything the solver cannot represent — multi-team trades, and trades where a
player has no salary row — and those exclusions are counted rather than dropped.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mironba.models.disposition import (
    AMBIGUOUS,
    BUYER,
    SELLER,
    disposition,
    standings_on,
)
from mironba.rules.constants import environment_for
from mironba.rules.in_season import trade_window
from mironba.rules.solver import Asset, TradeIntent, scan_targets, solve
from mironba.rules.trade_validator import TeamTradeState
from mironba.world.calendar import calendar_for

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
SEASON = "2024-25"
FREEZE = date(2025, 2, 5)

#: How many acquisition targets a team pursues. Bounded so the market stays a
#: market rather than every buyer bidding on every seller's roster.
SHORTLIST = 6

#: Prior-season value is what a deadline buyer actually reasons about, and it
#: is fully pre-freeze at any in-season date - the season being played has not
#: finished, so last season's totals leak nothing.
#: Derived, not enumerated. It was a hardcoded three-entry dict, so when seven
#: seasons were backfilled every one of them silently got an empty value map,
#: `_will_part_with` saw nothing worth keeping, and the planner proposed
#: **zero** trades for those seasons. A pooled backtest over ten seasons would
#: then have divided three seasons' proposals by ten seasons' positives and
#: reported it as a recall collapse.
_SEASONS = ("2015-16", "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
            "2021-22", "2022-23", "2023-24", "2024-25", "2025-26")
VALUE_SEASON = {s: _SEASONS[i - 1] for i, s in enumerate(_SEASONS) if i}


#: Median prior-season box_pm36 among valued players (1.04 in 2023-24). Above
#: it is a rotation piece a team in the race keeps; below it is the fringe.
FRINGE_VALUE = 1.04

#: Apply the part-with test to BOTH sides: the proposing team's outgoing
#: package must also survive its own disposition, not just the counterparty's.
#: Off by default - see ``docs/measurements.md`` entry 16 for the measurement
#: that decided it. Deterministic either way; no extra LLM calls.
SYMMETRIC_GATE = False


def _will_part_with(player_id: str, seller_side: str, values: dict) -> bool:
    """Whether a team on ``seller_side`` would actually give this player up.

    The value gate constrains only the acquiring side, and value here is close
    to zero-sum, so without this a supplier hands over anyone: the first run
    with values proposed Stephen Curry to Atlanta and Joel Embiid to Atlanta on
    the same tick. Both were legal. Neither is a trade.

    A seller parts with anyone - that is what selling is, and the cap relief
    and picks it gets back are real consideration this codebase does not price.
    A team still in the race parts only with a fringe player: a flyer or a
    salary filler, not a rotation piece. That asymmetry is the whole difference
    between a deadline market and a list of legal permutations.
    """
    if seller_side == SELLER:
        return True
    return values.get(player_id, 0.0) < FRINGE_VALUE


def player_values(season: str) -> dict:
    """Prior-season box_pm36 per Basketball-Reference id.

    The planner had no notion of value at all, so it ordered targets by cost
    and proposed Jayson Tatum and Payton Pritchard for Zion Williamson - legal,
    and absurd. This is the smallest honest fix: the value model fitted on
    seasons before the prior one, applied to the prior season's box score,
    matched to contract ids by normalised name.

    Nothing here touches the season being simulated. Using current-season
    totals would leak games played after the freeze, which is why the planner
    was given no value model in the first place.
    """
    import re
    import unicodedata

    prior = VALUE_SEASON.get(season)
    if prior is None:
        return {}
    try:
        from mironba.models.value import fit_value_model, load_player_seasons
        from mironba.models.win_delta import prior_seasons
    except Exception:  # noqa: BLE001
        return {}

    def norm(name):
        text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z]", "", text.lower())

    try:
        players = load_player_seasons()
    except FileNotFoundError:
        return {}
    all_seasons = sorted({p.season for p in players})
    train = tuple(prior_seasons(prior, all_seasons))
    if len(train) < 3:
        return {}
    model = fit_value_model(players, train)
    by_name = {
        norm(p.name): model.box_pm36(p)
        for p in players if p.season == prior and p.minutes > 0
    }
    names = _rows(SNAPSHOTS / f"bbref-{season}" / "players.csv")
    return {
        r["player_id"]: by_name[norm(r["name"])]
        for r in names if norm(r["name"]) in by_name
    }


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class DeadlineState:
    salary: dict[str, int]
    team_of: dict[str, str]
    payroll: dict[str, int]
    roster: dict[str, list[str]]
    names: dict[str, str]

    @classmethod
    def load(cls, season: str = SEASON) -> DeadlineState:
        contracts = _rows(SNAPSHOTS / f"bbref-{season}" / "contracts.csv")
        players = _rows(SNAPSHOTS / f"bbref-{season}" / "players.csv")
        salary = {r["player_id"]: int(r["salary"]) for r in contracts}
        team_of = {r["player_id"]: r["team_id"] for r in contracts}
        payroll: dict[str, int] = {}
        roster: dict[str, list[str]] = {}
        for row in contracts:
            payroll[row["team_id"]] = payroll.get(row["team_id"], 0) + int(row["salary"])
            roster.setdefault(row["team_id"], []).append(row["player_id"])
        return cls(salary, team_of, payroll, roster,
                   {r["player_id"]: r["name"] for r in players})

    def name(self, pid: str) -> str:
        return self.names.get(pid, pid)

    def assets(self, team: str) -> dict[str, Asset]:
        return {
            pid: Asset(pid, self.name(pid), self.salary[pid])
            for pid in self.roster.get(team, [])
        }

    def state(self, team: str) -> TeamTradeState:
        return TeamTradeState(
            team_id=team,
            team_salary=self.payroll.get(team, 0),
            # Roster size on the date is not in the ingest; 14 is the only
            # value that cannot manufacture a roster finding out of an input
            # we do not have. Same choice as eval/real_trades.py.
            roster_count=14,
        )


@dataclass
class ProposedTrade:
    buyer: str
    seller: str
    send: tuple[str, ...]
    receive: tuple[str, ...]

    @property
    def pair(self) -> frozenset:
        return frozenset((self.buyer, self.seller))


@dataclass
class DeadlineResult:
    proposals: list[ProposedTrade] = field(default_factory=list)
    dispositions: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    scanned: int = 0


def run(freeze: date | None = None, season: str = SEASON) -> DeadlineResult:
    """Every acquirer looks at every supplier's roster and takes what is legal.

    Deterministic, like the offseason planner and for the same reason: what is
    being scored is the solver against reality, and an LLM in the loop would
    make a miss unattributable.

    Buyers and ambiguous teams acquire; sellers and ambiguous teams supply. An
    ambiguous team parts only with a below-median player — see
    ``_will_part_with``, which is what stops a supplier handing over anyone.

    ``freeze`` defaults to the deadline **of the season asked for**. It used to
    default to a fixed 2025-02-05 regardless, so ``run(season="2025-26")``
    silently planned an offseason 365 days from its own deadline and returned
    nothing. Any freeze date may still be passed explicitly.
    """
    if freeze is None:
        freeze = calendar_for(season).deadline
    env = environment_for(season)
    world = DeadlineState.load(season)
    result = DeadlineResult()

    window = trade_window(freeze, season)
    result.notes.append(window.explain())

    result.dispositions = disposition(season, freeze)
    buyers = [t for t, d in result.dispositions.items() if d.side == BUYER]
    sellers = [t for t, d in result.dispositions.items() if d.side == SELLER]
    result.notes.append(
        f"{len(buyers)} buyers, {len(sellers)} sellers, "
        f"{sum(1 for d in result.dispositions.values() if d.side == AMBIGUOUS)} "
        "ambiguous (which act on both sides)"
    )

    values = player_values(season)
    result.notes.append(
        f"{len(values)} players valued from {VALUE_SEASON.get(season)} "
        "(prior completed season, fully pre-freeze)"
    )

    def value_of(pids):
        return sum(values.get(p, 0.0) for p in pids)

    # AMBIGUOUS teams act. They are not "teams that cannot decide" - they are
    # teams whose playoff direction is open, and those teams still consolidate
    # and take flyers. Standing pat was an artifact of the old value-model gate.
    ambiguous = sorted(
        t for t, d in result.dispositions.items() if d.side == AMBIGUOUS
    )
    acquirers = sorted(buyers + ambiguous)
    suppliers = sorted(sellers + ambiguous)
    result.notes.append(
        f"{len(acquirers)} acquiring ({len(buyers)} buyers + {len(ambiguous)} "
        f"ambiguous), {len(suppliers)} supplying ({len(sellers)} sellers + the "
        "same ambiguous teams, fringe players only)"
    )

    for buyer in acquirers:
        own = world.assets(buyer)
        for seller in suppliers:
            if seller == buyer:
                continue
            seller_side = result.dispositions[seller].side
            buyer_side = result.dispositions[buyer].side
            theirs = world.assets(seller)
            if not own or not theirs:
                continue
            scan = scan_targets(
                own=own, theirs=theirs,
                own_team=world.state(buyer), partner_team=world.state(seller),
                season=season, trade_date=freeze, max_assets_out=3,
            )
            result.scanned += 1
            if not scan.any_feasible:
                continue
            # Best acquirable player first, by prior-season value rather than
            # by cost. Ordering by cost is what produced Tatum-for-Zion.
            ranked = sorted(
                scan.targets, key=lambda t: -values.get(t.player_id, 0.0)
            )
            for target in ranked[:SHORTLIST]:
                solved = solve(
                    TradeIntent(
                        target_player_ids=(target.player_id,),
                        tradeable_asset_ids=tuple(own),
                    ),
                    own=own, theirs=theirs,
                    own_team=world.state(buyer), partner_team=world.state(seller),
                    season=season, trade_date=freeze, max_assets_out=3,
                )
                if not solved.satisfiable:
                    continue
                # Take the legal package that gains the most value, and only
                # if it gains any. A swap that sends out more than it brings
                # back is not a deadline move, however legal it is.
                if not _will_part_with(target.player_id, seller_side, values):
                    continue
                gains = [
                    (value_of(pkg.receive_player_ids) - value_of(pkg.send_player_ids), pkg)
                    for pkg in solved.packages
                    if not SYMMETRIC_GATE or all(
                        _will_part_with(pid, buyer_side, values)
                        for pid in pkg.send_player_ids
                    )
                ]
                gains = [g for g in gains if g[0] > 0]
                if not gains:
                    continue
                best = max(gains, key=lambda g: g[0])[1]
                result.proposals.append(
                    ProposedTrade(buyer, seller, best.send_player_ids,
                                  best.receive_player_ids)
                )
                break
    return result


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass
class DeadlineScore:
    proposed: int
    actual: int
    representable: int
    pair_hits: int
    #: Distinct *actual* trades matched by at least one proposal. Recall's
    #: numerator, and not the same number as ``pair_hits``: several proposals
    #: can hit one real trade, which made recall read 200% before this existed.
    actual_matched: int
    player_hits: int
    exact_hits: int
    solver_legal: int
    solver_scored: int

    @property
    def precision(self) -> float:
        return self.pair_hits / self.proposed if self.proposed else 0.0

    @property
    def recall(self) -> float:
        return self.actual_matched / self.representable if self.representable else 0.0


def actual_deadline_trades(season: str = SEASON, window_days: int = 14):
    """Two-team player trades in the run-up to the deadline.

    A window rather than the day itself, because on 2025-02-06 there were 13
    trades and **not one** was a two-team deal with players on both sides -
    every one was multi-team or picks-only. A validator that can only represent
    two-team trades therefore has an empty denominator on deadline day. The
    window is the smallest change that gives it anything to be scored against,
    and it is stated rather than tuned: 14 days back from the deadline.

    Read through eval/, which is the only place allowed to look at outcomes.
    """
    from mironba.eval.real_trades import parse_trades

    calendar = calendar_for(season)
    return [
        t for t in parse_trades(season)
        if 0 <= (calendar.deadline - t.when).days < window_days + 1
        and t.representable
    ]


def score(result: DeadlineResult, season: str = SEASON) -> DeadlineScore:
    from mironba.eval.real_trades import check

    actual = actual_deadline_trades(season)
    checks = [check(t) for t in actual]
    scored = [c for c in checks if c.scored]
    legal = [c for c in scored if c.legal]

    # A two-team proposal counts as a pair hit against a three-team trade when
    # both its teams were really in that trade. The planner cannot construct a
    # three-team deal, so demanding an exact team-set match would score it
    # against a shape it is structurally unable to produce - which measures the
    # scope limit, not the planner. Stated here because it is a scoring choice
    # that makes the number *easier*, and every such choice should be visible.
    actual_team_sets = [frozenset(t.teams) for t in actual]
    actual_players = {m.player_id for t in actual for m in t.moves}

    pair_hits = sum(
        1 for p in result.proposals
        if any(p.pair <= teams for teams in actual_team_sets)
    )
    actual_matched = sum(
        1 for teams in actual_team_sets
        if any(p.pair <= teams for p in result.proposals)
    )
    player_hits = sum(
        1 for p in result.proposals
        if set(p.receive) & actual_players or set(p.send) & actual_players
    )
    exact = 0
    for proposal in result.proposals:
        for t in actual:
            moved = {frozenset(t.sends(team)) for team in t.teams}
            if {frozenset(proposal.send), frozenset(proposal.receive)} <= moved:
                exact += 1
                break

    return DeadlineScore(
        proposed=len(result.proposals),
        actual=len(actual),
        representable=len(actual),
        pair_hits=pair_hits,
        actual_matched=actual_matched,
        player_hits=player_hits,
        exact_hits=exact,
        solver_legal=len(legal),
        solver_scored=len(scored),
    )


def leakage_audit(freeze: date = FREEZE, season: str = SEASON) -> list[str]:
    """What an in-season freeze exposes that an offseason one does not.

    Game results accumulate daily, so the surface is not a single snapshot
    date — it is every row of a table that grows while the scenario runs.
    """
    lines = []
    logs = SNAPSHOTS / "nba-stats" / "game_logs.csv"
    if logs.is_file():
        rows = [r for r in _rows(logs) if r["season"] == season]
        after = [r for r in rows if date.fromisoformat(r["GAME_DATE"]) > freeze]
        lines.append(
            f"game log: {len(rows)} team-games in {season}, {len(after)} after "
            f"the freeze and excluded by standings_on()"
        )
        standings = standings_on(season, freeze)
        played = sum(s.games_played for s in standings.values())
        lines.append(
            f"standings at freeze: {played} team-games counted, "
            f"{played / 2:.0f} games played of 1230"
        )
    stats = SNAPSHOTS / "nba-stats" / "player_seasons.csv"
    if stats.is_file():
        lines.append(
            f"player season totals: FULL-SEASON figures, so the {season} rows "
            "would leak games after the freeze. The planner reads only the "
            f"{VALUE_SEASON.get(season)} rows — a season that finished before "
            "the freeze, so complete by construction. The current season's "
            "totals are never touched, and disposition uses dated game logs "
            "rather than either."
        )
    contracts = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    if contracts.is_file():
        lines.append(
            "contracts: season cap hits with no date, so a player acquired "
            "AFTER the freeze still appears on his new team. That is the "
            "largest unfixable exposure in this scenario and it inflates the "
            "freeze rosters of every team that bought at the deadline."
        )
    tx = SNAPSHOTS / f"bbref-{season}" / "transactions.csv"
    if tx.is_file():
        rows = _rows(tx)
        after = [r for r in rows if date.fromisoformat(r["date"]) > freeze]
        lines.append(
            f"transaction log: {len(rows)} rows, {len(after)} after the freeze "
            "— read only through eval/, never by the planner"
        )
    return lines


def main(argv=None) -> int:
    from mironba.sim.tick import use_utf8_console

    use_utf8_console()
    parser = argparse.ArgumentParser(description="Run the 2025 deadline backtest.")
    parser.add_argument("--freeze", default=FREEZE.isoformat())
    args = parser.parse_args(argv)
    freeze = date.fromisoformat(args.freeze)

    world = DeadlineState.load()
    result = run(freeze)

    print("=" * 76)
    print(f"  DEADLINE BACKTEST — {SEASON}, frozen {freeze}")
    print("=" * 76)
    for note in result.notes:
        print(f"  {note}")

    print("\n  DISPOSITION")
    for side in (BUYER, SELLER):
        teams = sorted(t for t, d in result.dispositions.items() if d.side == side)
        print(f"    {side:9} {', '.join(teams) or 'none'}")

    print(f"\n  PROPOSED ({len(result.proposals)} from {result.scanned} "
          f"buyer-seller pairs)")
    for p in result.proposals:
        out = ", ".join(world.name(x) for x in p.send)
        inc = ", ".join(world.name(x) for x in p.receive)
        print(f"    {p.buyer} <- {p.seller}:  {inc}   (for {out})")

    actual = actual_deadline_trades()
    print(f"\n  ACTUAL two-team deadline trades ({len(actual)})")
    for t in actual:
        print(f"    {t.team_a}/{t.team_b}: {t.text[:88]}")

    s = score(result)
    print()
    print("=" * 76)
    print("  SCORED")
    print("=" * 76)
    print(f"  proposed {s.proposed}, actual {s.actual}")
    print(f"  counterparty pairs matched   {s.pair_hits}   "
          f"precision {s.precision:.1%}  recall {s.recall:.1%}")
    print(f"  any traded player involved   {s.player_hits}")
    print(f"  exact package reproduced     {s.exact_hits}")
    if s.solver_scored:
        print(f"  solver legality on actual    {s.solver_legal}/{s.solver_scored}")
    else:
        print("  solver legality on actual    n/a — none priceable")

    print()
    print("=" * 76)
    print("  IN-SEASON LEAKAGE AUDIT")
    print("=" * 76)
    for line in leakage_audit(freeze):
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
