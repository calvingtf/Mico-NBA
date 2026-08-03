"""Follow-on trades: the missing half of the chain-reaction goal.

A seeded trade produces a signing cascade already; this module makes it able
to produce further TRADES, under rules that are declared before anything
runs:

**Why the intent proposer is deterministic - a cost decision, not a
capability claim.** The GM-tick path uses an LLM for intent at ~30s a call;
thirty teams across cascade rounds would turn a 13-minute reaction into
hours. So intent here is proposed by rule, from the same feasible/unlock
machinery the LLM path is handed - the solver and validator are UNCHANGED,
and the LLM path stays intact for the intent A/B. Nothing about this module
claims a rule proposes better intents than a model; it claims the reaction
finishes today.

**The declared proposer rule.** A triggered team that is not a SELLER
targets the highest-salary player on a SELLER team's roster (top three
tried, solver decides feasibility); its tradeable assets are its own
contracts minus stipulation movers and minus its single highest-prior-salary
player (a team replacing a lost contest does not ship its best player to do
it); the persona's max_assets_out caps the package size. Priority is most
expendable first (ascending salary).

**Both sides must want it - without a market model.** Acceptance is the
supplier-side gate that already exists: ``models/disposition.py`` classifies
every team BUYER/SELLER/AMBIGUOUS from standings on the freeze date, and a
counterparty parts with a player only if it is a SELLER. No value-based
acceptance is modelled: the value model's 10.48-win resolution cannot
support one, and that is recorded as BLOCKED in the README boundary.
Candidates killed by this gate are counted and reported.

**Termination, declared not discovered.** Each team may EXECUTE at most one
generated trade per reaction, and each team gets at most one proposal
attempt; trigger depth is capped at MAX_DEPTH rounds. The report states the
depth actually reached and whether any cap bound.

**Stipulation integrity extends here.** No player named in a stipulation may
be targeted or sent in a generated trade; the enumerated invariant test
covers this path over every stipulated yaml.

Two-team trades only; k-team generation stays COSTED in the boundary.
Counterfactual cascades have no ground truth: the caller labels every output
UNFALSIFIABLE, and the only number quoted as a headline is the SEEDED-MINUS-
UNSEEDED difference - a cascade that would have happened anyway is not a
cascade.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date

from mironba.models.disposition import SELLER, disposition
from mironba.rules.solver import Asset, TradeIntent, solve
from mironba.rules.trade_validator import TeamTradeState

#: Declared caps. A trade emits trigger events that can cause more trades;
#: these are the stated bounds, not discovered ones.
MAX_DEPTH = 3
MAX_TARGET_TRIES = 3

TRADED = "player.traded"


@dataclass(frozen=True)
class GeneratedTrade:
    round: int
    acquirer: str
    counterparty: str
    received: tuple[str, ...]
    sent: tuple[str, ...]
    incoming_salary: int
    outgoing_salary: int
    trigger: str

    def key(self) -> frozenset:
        """Identity for the seeded-vs-unseeded diff: who moved where."""
        moves = {(pid, self.counterparty, self.acquirer) for pid in self.received}
        moves |= {(pid, self.acquirer, self.counterparty) for pid in self.sent}
        return frozenset(moves)

    def line(self, name=lambda p: p) -> str:
        got = ", ".join(name(p) for p in self.received)
        gave = ", ".join(name(p) for p in self.sent)
        return (f"    r{self.round} {self.acquirer} acquires {got} from "
                f"{self.counterparty} for {gave}  "
                f"(${self.incoming_salary:,} in / ${self.outgoing_salary:,} out)"
                f"  [{self.trigger}]")


@dataclass
class CascadeResult:
    trades: list = field(default_factory=list)
    attempts: int = 0
    killed_by_gate: int = 0
    killed_by_trade_rate_gate: int = 0
    killed_by_solver: int = 0
    suppressed_by_cap: int = 0
    depth_reached: int = 0
    wakes: int = 0
    polled_equivalent: int = 0

    @property
    def cap_bound(self) -> bool:
        return self.suppressed_by_cap > 0 or self.depth_reached >= MAX_DEPTH


def _payrolls(league) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in league.contracts_2627:
        out[row["team_id"]] = out.get(row["team_id"], 0) + int(row["salary"])
    return out


def _roster(league, team: str) -> dict[str, Asset]:
    return {
        row["player_id"]: Asset(row["player_id"], league.name(row["player_id"]),
                                int(row["salary"]))
        for row in league.contracts_2627 if row["team_id"] == team
    }


def _execute(league, acquirer: str, counterparty: str, package) -> None:
    dest = {pid: acquirer for pid in package.receive_player_ids}
    dest.update({pid: counterparty for pid in package.send_player_ids})
    for row in league.contracts_2627:
        if row["player_id"] in dest:
            row["team_id"] = dest[row["player_id"]]


def gate_by_trade_rate(team: str, revealed=None) -> bool:
    """True when a team may enter the generated-trade queue.

    trade_rate wired to propensity-to-deal: below the league-average rate in
    the sourced history means the team does not attempt a generated trade.
    SUGGESTIVE at n=11; arms built on this carry the label.
    """
    if revealed and revealed.get(team, {}).get("below_avg_trade_rate"):
        return False
    return True


def run_cascade(league, results, *, season: str, when: date,
                trade_season: str, teams, persona_for, scheduler,
                stipulated=frozenset(), revealed=None) -> CascadeResult:
    """Generated trades, triggered by the market's own events.

    ``results`` is the reaction's per-team outcome: a team enters the queue
    only because something touched it - it lost a contested player (the
    scheduler already emitted that SIGNED event and woke it). Executed trades
    emit TRADED events through the same scheduler, which wakes only teams
    whose registered interests they touch. Nothing polls.
    """
    sides = disposition(season, when)
    out = CascadeResult()

    hungry = [t for t in teams if results[t].lost_contests]
    queue = deque((t, f"lost contest for {results[t].lost_contests[0]}", 1)
                  for t in hungry)

    # Interest registration for TRADED wakes: a hungry team watches the
    # rosters it could plausibly target - every SELLER team's players.
    seller_players = {
        pid for t in teams if sides[t].side == SELLER
        for pid in _roster(league, t)
    }
    for t in hungry:
        scheduler.register(t, set(seller_players))

    attempted: set[str] = set()
    while queue:
        team, trigger, depth = queue.popleft()
        if depth > MAX_DEPTH:
            out.suppressed_by_cap += 1
            continue
        if team in attempted:
            out.suppressed_by_cap += 1
            continue
        attempted.add(team)
        if not gate_by_trade_rate(team, revealed):
            out.killed_by_trade_rate_gate += 1
            continue
        out.attempts += 1
        out.depth_reached = max(out.depth_reached, depth)

        if sides[team].side == SELLER:
            out.killed_by_gate += 1
            continue

        own = {pid: a for pid, a in _roster(league, team).items()
               if pid not in stipulated}
        if not own:
            continue
        # Declared untouchable: the team's best prior-salary player.
        best_own = max(own, key=lambda p: league.prior_salary.get(p, 0))
        persona = persona_for(team)

        # Candidate targets: highest-salary players on SELLER rosters, the
        # counterparty gate applied by construction (non-sellers are never
        # counterparties; the kill counter tracks pairs the gate removes).
        candidates = []
        for other in teams:
            if other == team:
                continue
            theirs = {pid: a for pid, a in _roster(league, other).items()
                      if pid not in stipulated}
            if not theirs:
                continue
            top = max(theirs.values(), key=lambda a: a.salary)
            if sides[other].side != SELLER:
                out.killed_by_gate += 1
                continue
            candidates.append((top.salary, other, top.player_id, theirs))
        candidates.sort(reverse=True)

        payrolls = _payrolls(league)
        executed = False
        for _, other, target, theirs in candidates[:MAX_TARGET_TRIES]:
            intent = TradeIntent(
                target_player_ids=(target,),
                tradeable_asset_ids=tuple(
                    sorted((p for p in own if p != best_own),
                           key=lambda p: own[p].salary)),
                excluded_player_ids=(best_own,),
                priority=tuple(sorted(own, key=lambda p: own[p].salary)),
                rationale=trigger,
            )
            result = solve(
                intent, own=own, theirs=theirs,
                own_team=TeamTradeState(team, payrolls.get(team, 0), len(own) + 1),
                partner_team=TeamTradeState(other, payrolls.get(other, 0),
                                            len(theirs)),
                season=trade_season, trade_date=when,
                max_assets_out=persona.max_assets_out,
            )
            packages = getattr(result, "packages", None) or []
            if not packages:
                out.killed_by_solver += 1
                continue
            package = packages[0]
            _execute(league, team, other, package)
            trade = GeneratedTrade(
                round=depth, acquirer=team, counterparty=other,
                received=tuple(package.receive_player_ids),
                sent=tuple(package.send_player_ids),
                incoming_salary=package.incoming_salary,
                outgoing_salary=package.outgoing_salary,
                trigger=trigger,
            )
            out.trades.append(trade)
            executed = True
            from mironba.sim.league import Event

            for pid in (*package.receive_player_ids, *package.send_player_ids):
                for woken in scheduler.wake_for(Event(TRADED, pid, team, "trade")):
                    queue.append((woken, f"trade moved {league.name(pid)}",
                                  depth + 1))
            break
        if not executed:
            continue

    out.wakes = scheduler.wakes
    out.polled_equivalent = scheduler.polled_equivalent
    return out
