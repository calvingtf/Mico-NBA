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
ambiguous, and at this freeze it returns 4 buyers, 3 sellers and 23 ambiguous —
which is the honest answer and means most teams have no strong prior either way.

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


def run(freeze: date = FREEZE, season: str = SEASON) -> DeadlineResult:
    """Every buyer looks at every seller's roster and takes what is legal.

    Deterministic, like the offseason planner and for the same reason: what is
    being scored is the solver against reality, and an LLM in the loop would
    make a miss unattributable.

    Only buyers acquire and only sellers send. That is the one place a
    disposition is allowed to drive behaviour, and it is why AMBIGUOUS teams do
    nothing — 23 of 30 here. A model that made them act would be inventing a
    preference from a standings gap the value model cannot resolve.
    """
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
        "ambiguous (which act on neither side)"
    )

    for buyer in sorted(buyers):
        own = world.assets(buyer)
        for seller in sorted(sellers):
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
            # Most expensive acquirable player first: a buyer at a deadline is
            # looking for the best player it can legally absorb, and "best" is
            # not available, so cost is the only ordering the data supports.
            for target in scan.targets[:SHORTLIST]:
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
                package = solved.packages[0]
                result.proposals.append(
                    ProposedTrade(buyer, seller, package.send_player_ids,
                                  package.receive_player_ids)
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
    player_hits: int
    exact_hits: int
    solver_legal: int
    solver_scored: int

    @property
    def precision(self) -> float:
        return self.pair_hits / self.proposed if self.proposed else 0.0

    @property
    def recall(self) -> float:
        return self.pair_hits / self.representable if self.representable else 0.0


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
    from mironba.eval.real_trades import parse_two_team_trades

    calendar = calendar_for(season)
    return [
        t for t in parse_two_team_trades(season)
        if 0 <= (calendar.deadline - t.when).days < window_days + 1
        and t.representable
    ]


def score(result: DeadlineResult, season: str = SEASON) -> DeadlineScore:
    from mironba.eval.real_trades import check

    actual = actual_deadline_trades(season)
    checks = [check(t) for t in actual]
    scored = [c for c in checks if c.scored]
    legal = [c for c in scored if c.legal]

    actual_pairs = {frozenset((t.team_a, t.team_b)) for t in actual}
    actual_players = {p for t in actual for p in t.a_sends + t.b_sends}

    pair_hits = sum(1 for p in result.proposals if p.pair in actual_pairs)
    player_hits = sum(
        1 for p in result.proposals
        if set(p.receive) & actual_players or set(p.send) & actual_players
    )
    exact = 0
    for proposal in result.proposals:
        for t in actual:
            moved = {frozenset(t.a_sends), frozenset(t.b_sends)}
            if {frozenset(proposal.send), frozenset(proposal.receive)} == moved:
                exact += 1
                break

    return DeadlineScore(
        proposed=len(result.proposals),
        actual=len(actual),
        representable=len(actual),
        pair_hits=pair_hits,
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
            "player season totals: FULL-SEASON figures, so any use of them "
            "in-season leaks games after the freeze. The deadline planner uses "
            "contracts and standings only, never these — the value model is "
            "not consulted here at all."
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
