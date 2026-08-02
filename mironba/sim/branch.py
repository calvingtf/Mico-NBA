"""Run both outcomes of a pending decision, and score the one that happened.

    python -m mironba.sim.branch

Scope is Golden State. Not because the machinery is team-specific, but because
the claim being tested is narrow and one team is enough to test it: given a
decision belonging to someone else, does a GM agent plan a coherent offseason
in each branch, and does the branch that actually happened resemble what the
team actually did.

The asymmetry between the branches is the point and it has to be stated up
front. One of them is what happened, and can be scored against the transaction
record. The other did not happen, has no ground truth, and will never have any
— there is no world where LeBron James both did and did not sign in Golden
State. It is reported as **unfalsifiable** and deliberately not scored. Its
value is comparative: what the same GM, from the same freeze state, under the
same seed, does differently when the decision goes the other way.

Everything the branches see comes from ``EvidenceLedger.world_state()`` and
snapshot rows dated on or before the freeze. The audit in ``leakage_audit``
re-checks that over every surface the branch touches rather than trusting that
it was checked once when the ledger was built.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from mironba.rules.constants import environment_for
from mironba.rules.signing import (
    BIRD,
    EARLY_BIRD,
    MINIMUM,
    NON_BIRD,
    FreeAgent,
    TeamCapState,
    signing_routes,
)
from mironba.rules.signing_solver import feasible_signings
from mironba.world.evidence import PRE, load_ledger, redact_after
from mironba.world.pending import (
    Block,
    Branch,
    OpportunityCost,
    Outcome,
    PendingDecision,
    build_branches,
)

DOCS = None
SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
BACKTEST = None
FREEZE = None
SEASON = None
PRIOR = None
SC = None
SUBJECT = ""
BLOCKER = ""
BLOCKER_BRANCH = ""
ACTUAL = ""
DECISION = None
GSW_BLOCK = None
POST_FREEZE: set = set()


def _prior_seasons(season: str, n: int = 3) -> tuple[str, ...]:
    """The n seasons before `season`, newest first ("2026-27" -> 2025-26...)."""
    start = int(season[:4])
    return tuple(f"{y}-{str(y + 1)[-2:]}" for y in range(start - 1, start - 1 - n, -1))


def bind_scenario(sc) -> None:
    """Bind every scenario-specific module global from one declared object.

    The reserved salary is DERIVED, not recalled: a maximum slot for a 10+
    years-of-service veteran is 35% of that season's cap, which is what
    holding "space for the subject" means in dollars.
    """
    global DOCS, BACKTEST, FREEZE, SEASON, PRIOR, SC, SUBJECT, BLOCKER
    global BLOCKER_BRANCH, ACTUAL, DECISION, GSW_BLOCK, POST_FREEZE
    global SERVICE_YEARS, _NAMES

    SC = sc
    DOCS = sc.evidence_dir
    BACKTEST = sc.id
    FREEZE = sc.freeze
    SEASON = sc.next_season
    PRIOR = sc.season
    SUBJECT = sc.decision_subject
    BLOCKER = sc.blocker_team
    BLOCKER_BRANCH = sc.blocker_branch
    ACTUAL = sc.actual_branch
    POST_FREEZE = sc.post_freeze_signing_ids()
    SERVICE_YEARS = {r["player_id"]: int(r["years"])
                     for r in sc._data_rows("service-years.csv")}
    _NAMES = {r["player_id"]: r["name"] for r in sc._data_rows("names.csv")}

    DECISION = PendingDecision(
        decision_id=f"{sc.id}-destination",
        owner=SUBJECT,
        question=sc.decision,
        outcomes=tuple(
            Outcome(key,
                    f"{_name(SUBJECT)}: {key.replace(chr(95), chr(32))}",
                    (BLOCKER,) if key == BLOCKER_BRANCH else ())
            for key in sc.branches
        ),
        opened_on=sc.freeze,
    )
    env = environment_for(SEASON)
    GSW_BLOCK = Block(
        team=BLOCKER,
        decision_id=DECISION.decision_id,
        awaiting_outcome=BLOCKER_BRANCH,
        reserved_salary=int(env.salary_cap * 0.35),
        reserved_roster_spots=1,
        opportunity_cost=OpportunityCost(
            lost_targets=(),
            note=(
                f"{BLOCKER} committed nothing new between the freeze and the "
                "decision. The cost of waiting is the free agents who came "
                "off the board meanwhile; the ones this simulation can name "
                "are filled in from the transaction log at run time."
            ),
        ),
    )


@dataclass
class PlannedMove:
    """One move a branch's GM made, with terms produced by the solver."""

    player_id: str
    name: str
    route: str
    first_year_salary: int
    years: int
    hard_cap: str | None = None

    def line(self) -> str:
        cap = f"  [hard cap: {self.hard_cap}]" if self.hard_cap else ""
        return (
            f"  {self.name:<24} ${self.first_year_salary:>12,}  "
            f"via {self.route:<16} {self.years}yr{cap}"
        )


@dataclass
class BranchResult:
    branch: Branch
    moves: list[PlannedMove] = field(default_factory=list)
    committed_after: int = 0
    roster_after: int = 0
    notes: list[str] = field(default_factory=list)
    #: Set only for the branch that actually happened.
    falsifiable: bool = False

    @property
    def label(self) -> str:
        return self.branch.label


# --------------------------------------------------------------------------
# The freeze state
# --------------------------------------------------------------------------


def gsw_freeze_state() -> tuple[TeamCapState, list[FreeAgent], dict[str, int]]:
    """Golden State as of the freeze, from snapshot rows only.

    The roster is everyone under contract for 2026-27 who is *not* one of the
    players whose signing happened after the freeze — those are outcomes, and
    seeding them would be scoring the simulation on its own inputs.
    """
    contracts = SNAPSHOTS / f"bbref-contracts-{SEASON}" / "contract_years.csv"
    with contracts.open(encoding="utf-8", newline="") as handle:
        rows = [
            r for r in csv.DictReader(handle)
            if r["team_id"] == BLOCKER and r["season"] == SEASON
        ]

    # Post-freeze signings, which are the answer rather than the setup.
    post_freeze = set(POST_FREEZE)
    held = [r for r in rows if r["player_id"] not in post_freeze]
    state = TeamCapState(
        team_id=BLOCKER,
        season=SEASON,
        committed_salary=sum(int(r["salary"]) for r in held),
        roster_count=len({r["player_id"] for r in held}),
    )

    # Prior-season salaries, for the Bird-family ceilings.
    prior_path = SNAPSHOTS / f"bbref-{PRIOR}" / "contracts.csv"
    prior: dict[str, int] = {}
    team_2526: dict[str, str] = {}
    with prior_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            prior[row["player_id"]] = int(row["salary"])
            team_2526[row["player_id"]] = row["team_id"]

    # Rights are counted from the snapshots, not assumed. The observable window
    # is three seasons, which happens to be exactly the Bird threshold — a
    # player with more tenure than that is indistinguishable from one with
    # exactly three, and both are Bird.
    def years_with(pid: str) -> int:
        years = 0
        for season in _prior_seasons(SEASON):
            path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
            if not path.is_file():
                break
            with path.open(encoding="utf-8", newline="") as handle:
                on_team = any(
                    r["player_id"] == pid and r["team_id"] == BLOCKER
                    for r in csv.DictReader(handle)
                )
            if on_team:
                years += 1
            else:
                break
        return years

    # The candidate pool is wider than the five who re-signed, or precision
    # would be 1.0 by construction: a planner that can only pick players who
    # actually signed cannot propose a move that did not happen. Golden State's
    # own 2025-26 players with no 2026-27 deal anywhere are genuine free agents
    # it could have kept, and four of them exist — Gary Payton II among them,
    # whom the reporting expected back and who never appears on the books.
    under_contract_2627 = set()
    with contracts.open(encoding="utf-8", newline="") as handle:
        under_contract_2627 = {r["player_id"] for r in csv.DictReader(handle)}
    unsigned_own = {
        pid for pid, team in team_2526.items()
        if team == BLOCKER and pid not in under_contract_2627
    }

    agents = [
        FreeAgent(
            player_id=pid,
            name=_name(pid),
            years_of_service=SERVICE_YEARS.get(pid, MINIMUM_CAP_HIT_TIER),
            prior_salary=prior.get(pid, 0),
            years_with_team=years_with(pid),
        )
        for pid in sorted(post_freeze | unsigned_own)
    ]
    return state, agents, prior


_NAMES: dict = {}


def _name(pid: str) -> str:
    return _NAMES.get(pid, pid)


#: Veteran-minimum contracts hit the cap at the two-year tier regardless of how
#: long the player has actually served — the league reimburses the difference.
#: So a pool member whose service is not separately sourced is priced at this
#: tier, which is a convention about cap accounting rather than a claim about
#: his career.
MINIMUM_CAP_HIT_TIER = 2

#: Service years, hand-supplied and flagged. The performance ingest starts at
#: 2014-15 so it cannot count a career that began earlier, and Basketball-
#: Reference's contract page does not publish service. These come from public
#: draft years and are the one recalled input in this branch — recorded here
#: rather than inlined so the flag travels with the number.
SERVICE_YEARS: dict = {}
SERVICE_PROVENANCE = "hand-supplied in the scenario store (service-years.csv); not from any ingest"


# --------------------------------------------------------------------------
# Branch execution
# --------------------------------------------------------------------------


def plan_branch(
    branch: Branch,
    state: TeamCapState,
    agents: list[FreeAgent],
    env,
    *,
    budget: int | None = None,
) -> BranchResult:
    """Plan Golden State's offseason under one outcome.

    Deterministic in this build. That is a placeholder for an LLM agent and it
    is honest about being one: what is being tested here is the branch
    machinery and the solver, not a model's judgement, and mixing the two would
    make a failure unattributable.

    **The budget is what makes this a decision rather than an inventory.**
    Without a ceiling the planner re-signs everyone it can afford and
    "retention" is near-tautological — it would have to fail on cap grounds to
    miss anyone, and Bird rights mean it almost never does. The default ceiling
    is the second apron, which is a real cliff rather than a taste: crossing it
    freezes a team's first-round pick seven years out, bans salary aggregation
    in trades, and removes the taxpayer mid-level. Teams observably behave as
    though it binds.

    With a ceiling the planner must drop somebody, and which somebody is a
    choice the result can be scored on.
    """
    result = BranchResult(branch=branch)
    committed = state.committed_salary
    roster = state.roster_count
    ceiling = env.second_apron if budget is None else budget
    result.notes.append(f"budget ceiling ${ceiling:,} (second apron)")

    won = any(b.capacity_used for b in branch.blocks if b.team == BLOCKER)
    if won:
        # James signs here: the reserved slot is spent on him, and the money
        # that was being held is gone.
        block = next(b for b in branch.blocks if b.team == BLOCKER)
        lebron = FreeAgent(
            SUBJECT, _name(SUBJECT),
            years_of_service=SERVICE_YEARS[SUBJECT],
            prior_salary=0, years_with_team=0,
        )
        routes = signing_routes(
            TeamCapState(BLOCKER, SEASON, committed_salary=committed,
                         roster_count=roster),
            lebron, env,
        )
        best = routes.best()
        if best is not None:
            result.moves.append(
                PlannedMove(SUBJECT, _name(SUBJECT), best.route,
                            best.max_first_year, best.max_years, best.hard_cap)
            )
            committed += best.max_first_year
            roster += 1
        result.notes.append(
            f"reserved ${block.reserved_salary:,} was used"
        )
    else:
        result.notes.append(
            "the reserved slot was never used; the wait bought nothing"
        )

    for agent in sorted(agents, key=lambda a: -a.prior_salary):
        team_now = TeamCapState(
            BLOCKER, SEASON, committed_salary=committed, roster_count=roster
        )
        routes = signing_routes(team_now, agent, env)
        if not routes.any_route:
            result.notes.append(f"no route for {agent.name}: {routes.explain()[:70]}")
            continue
        # The most the team could pay him by any route that still fits under
        # the ceiling.
        #
        # Not the cheapest: routing every player to the minimum put Draymond
        # Green on $3.88M, which is not a plan, it is an artifact of asking the
        # wrong question. This model has no player-demand side, so it cannot
        # know what a player would accept — what it can say is which route the
        # team commits and what that route permits. Budgeting against the
        # maximum makes the ceiling bind conservatively, which is what forces a
        # genuine drop rather than a universal downgrade.
        affordable = [
            r for r in routes.routes
            if committed + r.max_first_year <= ceiling
        ]
        if not affordable:
            cheapest = min(routes.routes, key=lambda r: r.max_first_year)
            result.notes.append(
                f"dropped {agent.name}: cheapest route is "
                f"{cheapest.route} at ${cheapest.max_first_year:,}, which would "
                f"put the team ${committed + cheapest.max_first_year - ceiling:,} "
                "over the ceiling"
            )
            continue
        best = max(affordable, key=lambda r: r.max_first_year)
        result.moves.append(
            PlannedMove(agent.player_id, agent.name, best.route,
                        best.max_first_year, best.max_years, best.hard_cap)
        )
        committed += best.max_first_year
        roster += 1

    result.committed_after = committed
    result.roster_after = roster
    return result


# --------------------------------------------------------------------------
# Leakage audit
# --------------------------------------------------------------------------


@dataclass
class AuditLine:
    surface: str
    checked: int
    rejected: int
    detail: str

    def line(self) -> str:
        mark = "OK  " if self.rejected == 0 or "excluded" in self.detail else "!!  "
        return f"  {mark}{self.surface:<38} {self.checked:>6} checked  {self.detail}"


def leakage_audit(freeze: date | None = None) -> list[AuditLine]:
    """Re-check the freeze over everything a branch touches.

    Not a re-read of the ledger's own flag. Each surface is examined for dates
    after the freeze and for the specific post-freeze facts that would matter,
    because the ledger only guards the evidence file and a branch reads more
    than that.
    """
    freeze = freeze or FREEZE
    lines: list[AuditLine] = []
    ledger = load_ledger(DOCS, BACKTEST, freeze)

    world = ledger.world_state()
    lines.append(AuditLine(
        "evidence file (PRE partition)", len(world),
        sum(1 for i in world if i.date > freeze),
        f"{len(ledger.items) - len(world)} POST items withheld; "
        f"max date {max((i.date for i in world), default='-')}",
    ))

    live = ledger.open_conditionals()
    lines.append(AuditLine(
        "conditional commitments", len(live),
        sum(1 for c in live if c.date > freeze),
        f"{len(ledger.conditionals) - len(live)} POST commitments withheld",
    ))

    tx = SNAPSHOTS / f"bbref-{PRIOR}" / "transactions.csv"
    if tx.is_file():
        with tx.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        kept = redact_after(rows, freeze, key="date")
        lines.append(AuditLine(
            f"transaction log ({PRIOR})", len(rows), len(rows) - len(kept),
            f"{len(rows) - len(kept)} post-freeze row(s) excluded by redact_after; "
            f"latest kept {max((r['date'] for r in kept), default='-')}",
        ))

    stats = SNAPSHOTS / "nba-stats" / "player_seasons.csv"
    if stats.is_file():
        with stats.open(encoding="utf-8", newline="") as handle:
            seasons = {r["season"] for r in csv.DictReader(handle)}
        future = {s for s in seasons if s >= SEASON}
        lines.append(AuditLine(
            "player performance ingest", len(seasons), len(future),
            f"seasons {min(seasons)}..{max(seasons)}; none is {SEASON} or later, "
            "so no on-court result from the season being projected is readable"
            if not future else f"LEAKS: {sorted(future)}",
        ))

    contracts = SNAPSHOTS / f"bbref-contracts-{SEASON}" / "contract_years.csv"
    if contracts.is_file():
        with contracts.open(encoding="utf-8", newline="") as handle:
            gsw = [
                r for r in csv.DictReader(handle)
                if r["team_id"] == BLOCKER and r["season"] == SEASON
            ]
        post = set(POST_FREEZE)
        seeded = [r for r in gsw if r["player_id"] in post]
        lines.append(AuditLine(
            f"contract snapshot ({BLOCKER} roster)", len(gsw), len(seeded),
            f"{len(seeded)} post-freeze signing(s) present in the file and "
            "excluded from the freeze state by gsw_freeze_state(); "
            "world/dated_roster.py reconstructs dated presence (validated "
            "138/138 must-present) and world/contract_expiry.py resolves "
            "June-30 expiry (validated, ~0.6% false-free). Season-table "
            "inflation measured at $126,157,001 / 2.27% - real, but NOT what "
            "bound the suitor check: the binding constraint was ROSTER COUNT "
            "read off table bloat, a diagnosis corrected only when the "
            "route-blocked reasons were finally read. Remaining unmodelled: "
            "cap holds of expired contracts, and rosters a GM could clear "
            "but has not - the filter tests the roster as it stands",
        ))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run both branches of a declared pending-decision scenario.")
    parser.add_argument("--scenario", required=True,
                        help="a declared scenario id under configs/branch/")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    from mironba.world.scenario import load_scenario

    bind_scenario(load_scenario(args.scenario))
    env = environment_for(SEASON)
    state, agents, _ = gsw_freeze_state()
    agents = [
        FreeAgent(a.player_id, a.name, SERVICE_YEARS.get(a.player_id, a.years_of_service),
                  a.prior_salary, a.years_with_team)
        for a in agents
    ]
    ledger = load_ledger(DOCS, BACKTEST, FREEZE)
    branches = build_branches(DECISION, [GSW_BLOCK], ledger.open_conditionals(),
                              fires=SC.condition_fires_in)

    print("=" * 74)
    print(f"  freeze {FREEZE}   {BLOCKER} at ${state.committed_salary:,}, "
          f"{state.roster_count} under contract")
    print("=" * 74)

    results = []
    for branch in branches:
        result = plan_branch(branch, state, agents, env)
        result.falsifiable = branch.outcome_key == ACTUAL
        results.append(result)
        print(f"\nBRANCH {branch.label}")
        print(f"  active commitments: "
              f"{[c.id for c in branch.active_commitments] or 'none'}")
        for block in branch.blocks:
            print(f"  {block.describe()}")
        for move in result.moves:
            print(move.line())
        print(f"  -> ${result.committed_after:,}, {result.roster_after} players")
        for note in result.notes:
            print(f"  note: {note}")

    actual_branch = next(r for r in results if r.branch.outcome_key == ACTUAL)
    print("\n" + "=" * 74)
    print(f"  SCORING - {ACTUAL} is what happened")
    print("=" * 74)
    from mironba.eval.branch_score import Tally, actual_routes, score_moves

    scores = score_moves(
        {m.player_id for m in actual_branch.moves}, state, agents, env,
        scenario=SC,
    )
    for score in scores:
        print(score.line())
    exact = [s for s in scores if s.admits_exact_check]
    ceiling_only = [s for s in scores if not s.admits_exact_check]
    print(f"\n  route {sum(s.route_hit for s in scores)}/{len(scores)}")
    print(f"  exact-figure checks   {sum(1 for s in exact if s.exact_hit)}/{len(exact)}"
          f"   ({', '.join(s.player for s in exact) or 'none'})")
    print(f"  ceiling-only checks   {sum(1 for s in ceiling_only if s.within_max)}"
          f"/{len(ceiling_only)}   ({', '.join(s.player for s in ceiling_only)})")

    tally = Tally(
        proposed=[m.player_id for m in actual_branch.moves],
        actual=list(actual_routes(SC)),
    )
    print("\n  PRECISION AND RECALL")
    print(tally.render(name=_name))
    print("\n  Retention is no longer near-tautological: the planner works to a")
    print("  second-apron ceiling and had to drop players to stay under it.")

    counterfactual = next(r for r in results if r.branch.outcome_key != ACTUAL)
    print(f"\n  BRANCH {counterfactual.label} IS NOT SCORED.")
    print("  It did not happen and never will have ground truth. Its value is")
    print("  comparative: same GM, same freeze state, different answer.")

    print("\n" + "=" * 74)
    print("  LEAKAGE AUDIT")
    print("=" * 74)
    for line in leakage_audit():
        print(line.line())

    if args.out:
        args.out.write_text(json.dumps({
            "freeze": FREEZE.isoformat(),
            "branches": [
                {"label": r.label, "moves": [asdict(m) for m in r.moves],
                 "committed_after": r.committed_after,
                 "falsifiable": r.falsifiable,
                 "commitments": [c.id for c in r.branch.active_commitments]}
                for r in results
            ],
            "scores": [asdict(s) for s in scores],
            "audit": [asdict(a) for a in leakage_audit()],
        }, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
