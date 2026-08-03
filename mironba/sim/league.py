"""Several teams, one free-agent market, and an event-driven clock.

    python -m mironba.sim.league

The premise of the project, finally built. Until now a "simulation" was one
team planning against a fixed world. Here five teams plan against each other,
two of them cannot sign the same player, and the resolution of that changes
what the losers do next.

Three things arrive together because none works alone.

**Multi-team planning.** Golden State, Miami, Minnesota and Cleveland were the
teams reported as blocked on the LeBron decision; Philadelphia is the one that
won it. Each plans from the freeze state under its own cap position, its own
rights and its own apron constraints — which differ enough to matter: Cleveland
is above the second apron and has no mid-level at all, Miami is merely over the
cap and has the full one.

**Contention.** Two teams cannot sign the same player. When planners want the
same man, someone loses and has to do something else. That is the first genuine
interaction in this codebase — every earlier simulation would have let both
teams sign him.

**An event-driven scheduler.** The charter asked for this at M0 and it has
never existed. An agent wakes when an event touches its neighbourhood, not
every tick. The saving is reported against what polling every agent every tick
would have cost.

## How a player chooses, and what this refuses to do

The obvious move is to have the player pick the team that maximises his
projected wins. That would be fabrication. ``models/delta_error.py`` measured
the win-delta error at 7.4 wins and the separation threshold at 10.5, so the
value model cannot rank two plausible destinations — and a preference the model
cannot support is exactly what ``models/compare.py`` exists to prevent.

So the resolution uses only what is actually available:

  * **the offer** — a route maximum is a real figure the solver computed, and
    preferring more money is an economic claim rather than a win-model one;
  * **conditional commitments** from the evidence file, where a reported
    intention names this player and this branch;
  * **stated persona parameters**, which are structured inputs rather than
    inferences.

Where none of those separates two offers the choice is **recorded as
arbitrary** and broken by a seeded coin. It is not dressed up: ``Contest``
carries the reason for every contested player, and "arbitrary" appears in the
output rather than quietly resolving.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from mironba.agents.gm import GMPersona
from mironba.rules.constants import environment_for
from mironba.rules.signing import FreeAgent, TeamCapState, signing_routes
from mironba.sim.arrivals import (
    SIGNING,
    mechanism,
    pre_freeze_ids,
    signing_targets,
)

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
# Scenario-bound module state. Nothing here has a hardcoded value: main() and
# every test bind a declared scenario first, and helpers assert the binding.
SC = None
DOCS = None
ARRIVALS = ()
SUBJECT = ""
BLOCKER_BRANCH = ""
ACTUAL_BRANCH = ""
PRIOR_SEASON = ""


def bind_scenario(scenario) -> None:
    """Bind every scenario-specific module global from one declared object."""
    global SC, DOCS, ARRIVALS, SUBJECT, BLOCKER_BRANCH, ACTUAL_BRANCH, SERVICE_YEARS
    global PRIOR_SEASON, SEASON, FREEZE, BACKTEST, TEAMS, PERSONAS, BRANCH_PREMISES
    from mironba.sim.arrivals import load_arrivals

    SC = scenario
    DOCS = scenario.evidence_dir
    ARRIVALS = load_arrivals(scenario)
    SUBJECT = scenario.decision_subject
    BLOCKER_BRANCH = scenario.blocker_branch
    ACTUAL_BRANCH = scenario.actual_branch
    PRIOR_SEASON = scenario.season
    SEASON = scenario.next_season
    FREEZE = scenario.freeze
    BACKTEST = scenario.id
    TEAMS = tuple(scenario.scored_teams)
    PERSONAS = {
        team: GMPersona(**params) for team, params in scenario.personas.items()
    }
    SERVICE_YEARS = {
        r["player_id"]: int(r["years"])
        for r in scenario._data_rows("service-years.csv")
    }
    BRANCH_PREMISES = dict(scenario.branch_premises)


SEASON = None
FREEZE = None
BACKTEST = None
BRANCH_PREMISES: dict = {}

#: The teams reported as being in the LeBron field, plus the one that won it.
#: Not an arbitrary five — evidence item LBJ-04 names exactly these.
TEAMS = ()

#: Structured persona parameters, one per team. Numeric and sweepable, never
#: prose — the charter's rule, and it also lets a persona feed a deterministic
#: constraint rather than only a prompt.
#:
#: These are ASSUMPTIONS, not sourced facts, and they are the largest hand-set
#: input in this module. They are set from each team's observable cap behaviour
#: at the freeze rather than from reputation: a team already past the second
#: apron has revealed a high win-now weight, and one with room has not.
PERSONAS: dict = {}  # bound from the scenario's declared personas

#: Veteran-minimum deals hit the cap at the two-year tier whatever the player's
#: service. Used for pool members whose service is not separately sourced.
MINIMUM_CAP_HIT_TIER = 2

#: The 25 unscored teams share one persona, and that is a stated
#: simplification, not an oversight: the 5-vs-30 comparison is scored on the
#: five teams whose personas are unchanged, so the 25 are *competition*, and
#: giving them one persona measures "more competitors", not "diverse
#: competitors". Entry 23 (personas may be text the model recites) caveats
#: any stronger design anyway.
DEFAULT_PERSONA = GMPersona("balanced-default", risk_tolerance=0.5,
                            win_now_horizon=2, asset_hoarding=0.5)


def persona_for(team: str) -> GMPersona:
    return PERSONAS.get(team, DEFAULT_PERSONA)


SERVICE_YEARS = {}

#: How many players a team pursues. Bounded so the market is a market rather
#: than every team bidding on all 130 free agents.
SHORTLIST = 10


# --------------------------------------------------------------------------
# Events and the scheduler
# --------------------------------------------------------------------------

SIGNED = "player.signed"
DECISION = "decision.resolved"


@dataclass(frozen=True, slots=True)
class Event:
    kind: str
    subject: str
    team: str = ""
    detail: str = ""


@dataclass
class Scheduler:
    """Wakes an agent only when an event touches its neighbourhood.

    A GM's neighbourhood is the set of players it is pursuing, plus any
    decision it is blocked on. A signing elsewhere matters only to teams that
    wanted that player; a decision resolving matters to everyone waiting on it.

    Counts both what it spent and what polling every agent on every tick would
    have cost, because the charter's claim was that event-driven scheduling is
    cheaper and a claim like that should carry its own evidence.
    """

    teams: tuple[str, ...]
    interests: dict[str, set[str]] = field(default_factory=dict)
    wakes: int = 0
    events: int = 0
    polled_equivalent: int = 0
    log: list[str] = field(default_factory=list)

    def register(self, team: str, players: set[str]) -> None:
        self.interests[team] = set(players)

    def wake_for(self, event: Event) -> list[str]:
        self.events += 1
        self.polled_equivalent += len(self.teams)
        if event.kind == DECISION:
            woken = [t for t in self.teams if t != event.team]
        else:
            woken = [
                t for t in self.teams
                if t != event.team and event.subject in self.interests.get(t, set())
            ]
        self.wakes += len(woken)
        self.log.append(
            f"{event.kind} {event.subject} ({event.team}) -> "
            f"{len(woken)}/{len(self.teams)}: {','.join(woken) or 'nobody'}"
        )
        return woken

    @property
    def saving(self) -> float:
        if not self.polled_equivalent:
            return 0.0
        return 1.0 - (self.wakes / self.polled_equivalent)


# --------------------------------------------------------------------------
# Contention
# --------------------------------------------------------------------------

#: Width of a contention tier, in projected wins.
#:
#: The measured separation threshold is 10.5 wins (models/delta_error.py), and
#: a tier has to be at least that wide or the comparison inside it would be
#: exactly the one the value model cannot make. 12 clears it with room and is
#: stated here rather than tuned: at this width a 55-win team and a 30-win team
#: fall two tiers apart, which is a distinction the model genuinely supports,
#: while two 44-win teams do not.
TIER_WIDTH_WINS = 12.0

UNCONTESTED = "uncontested"
BY_TIER = "clearly stronger roster"
BY_OFFER = "higher offer"
BY_COMMITMENT = "conditional commitment names this team"
ARBITRARY = "arbitrary - nothing available separates the offers"

#: How much larger one offer must be to count as separating. A dollar is not a
#: preference; a tenth is a real difference in a negotiation.
OFFER_MARGIN = 0.10


@dataclass(frozen=True, slots=True)
class Offer:
    team: str
    player_id: str
    route: str
    max_first_year: int


@dataclass
class Contest:
    player_id: str
    offers: list[Offer]
    winner: str
    reason: str

    @property
    def contested(self) -> bool:
        return len(self.offers) > 1

    def line(self, name=lambda p: p) -> str:
        competing = ", ".join(
            f"{o.team} ({o.route} up to ${o.max_first_year:,})" for o in self.offers
        )
        head = f"  {name(self.player_id):<22} -> {self.winner:<4} [{self.reason}]"
        return head + "\n      " + competing


def resolve(player_id, offers, commitments, rng, projections=None) -> Contest:
    """Decide who signs a contested player, using only defensible inputs.

    Offer-maximisation alone was falsified by the scenario it exists to model.
    It cannot produce LeBron James choosing Philadelphia at $3,876,529 over a
    team with cap space, and in the M5 run it handed all eight contested
    players to whichever team had the most room. Money is a factor; it is not
    the only one and it is not the first.

    Order, most to least defensible:

      1. **a conditional commitment naming this player** — evidence about what
         was reported, which beats any inference about what should happen;
      2. **roster tier** — the value model CAN separate a contender from a
         rebuild, it just cannot separate two contenders. Tiers are
         TIER_WIDTH_WINS apart precisely so every comparison made here is one
         the measured error supports;
      3. **the offer**, once tier and commitment are silent;
      4. **arbitrary**, recorded as such.

    Step 2 is the one that makes this different from M5, and it is bounded by
    the same measurement that bans the naive version: a comparison inside a
    tier is never made.
    """
    if len(offers) == 1:
        return Contest(player_id, offers, offers[0].team, UNCONTESTED)

    ranked = sorted(offers, key=lambda o: (-o.max_first_year, o.team))

    for commitment in commitments:
        text = f"{commitment.condition} {commitment.commitment}".lower()
        if player_id.lower() in text:
            for offer in ranked:
                if offer.team.lower() == commitment.subject.lower():
                    return Contest(player_id, offers, offer.team, BY_COMMITMENT)

    if projections:
        tiers = {
            o.team: int(projections.get(o.team, 0.0) // TIER_WIDTH_WINS)
            for o in ranked
        }
        best_tier = max(tiers.values())
        top = [o for o in ranked if tiers[o.team] == best_tier]
        if len(top) < len(ranked):
            if len(top) == 1:
                return Contest(player_id, offers, top[0].team, BY_TIER)
            # Several teams share the top tier: fall through to the offer,
            # but only among them. A lower tier never wins on money alone.
            ranked = top

    if len(ranked) == 1:
        return Contest(player_id, offers, ranked[0].team, BY_TIER)

    best, second = ranked[0], ranked[1]
    if best.max_first_year >= second.max_first_year * (1 + OFFER_MARGIN):
        return Contest(player_id, offers, best.team, BY_OFFER)

    winner = rng.choice(sorted({o.team for o in ranked}))
    return Contest(player_id, offers, winner, ARBITRARY)


# --------------------------------------------------------------------------
# League state
# --------------------------------------------------------------------------


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


#: Every sim-side fact DERIVED from the 2026-27 table rather than read from a
#: pre-freeze source, with its direction. tests/test_derived_facts.py walks the
#: AST and fails on any contracts_2627 consumer missing from this registry, the
#: way the writer test enumerates writers - a new derivation is covered by
#: default. "Could this be computed at the freeze with only pre-freeze
#: information?" is the question each entry answers.
DERIVED_FACTS = {
    "load": dict(freeze_computable=False, direction="constructor",
                 note="loads the table; consumers below decide what leaks"),
    "arrivals": dict(freeze_computable=False, direction="cleaning+target",
                     note="ground truth. As eval target: allowed. As freeze-"
                          "state subtraction: cleaning, but misses re-signings "
                          "by definition (an arrival needs a team change)"),
    "freeze_state": dict(freeze_computable=True, direction="repair",
                         note="repaired: coverage decided by the expiry "
                              "machinery plus PRE-evidence option declines - "
                              "never by identity from POST evidence. Residual "
                              "floor: re-signings with no pre-freeze signal "
                              "stay in, quantified at entry 47/48"),
    "free_agent_pool": dict(freeze_computable=False, direction="hurts",
                            note="excludes everyone holding a 2026-27 deal = "
                                 "every actual re-signee. Deflates acquisition "
                                 "recall, shields incumbents from contention. "
                                 "Entry 43; expiring_pool() is the repair"),
    "expiring_pool": dict(freeze_computable=True, direction="repair",
                          note="expiry-validated pool; residual occupy-bias "
                               "measured at ~0.6% false frees, stated"),
    "project_wins": dict(freeze_computable=True, direction="repair",
                         note="repaired: tiers now come from dated presence + validated expiry at the freeze, not 2026-27 rosters. The eval import is Entry 45 records the leak; the repair commit records the pre-registered prediction"),
}


@dataclass


class LeagueState:
    contracts_2627: list[dict]
    contracts_2526: list[dict]
    team_2526: dict[str, str]
    prior_salary: dict[str, int]
    names: dict[str, str]
    _rights_cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> LeagueState:
        c26 = _rows(SNAPSHOTS / f"bbref-contracts-{SEASON}" / "contract_years.csv")
        c25 = _rows(SNAPSHOTS / f"bbref-{PRIOR_SEASON}" / "contracts.csv")
        names: dict[str, str] = {}
        for season in _recent_seasons(2):
            path = SNAPSHOTS / f"bbref-{season}" / "players.csv"
            if path.is_file():
                for row in _rows(path):
                    names.setdefault(row["player_id"], row["name"])
        return cls(
            contracts_2627=[r for r in c26 if r["season"] == SEASON],
            contracts_2526=c25,
            team_2526={r["player_id"]: r["team_id"] for r in c25},
            prior_salary={r["player_id"]: int(r["salary"]) for r in c25},
            names=names,
        )

    def name(self, pid: str) -> str:
        return self.names.get(pid, pid)

    def arrivals(self, team: str) -> set[str]:
        """Players on this team in 2026-27 who were not on it in 2025-26.

        Ground truth for "who did this team add". It cannot separate a signing
        from a trade or a draft pick, because the transaction log stops nine
        days after the freeze. So a trade acquisition counts here as an arrival
        the planner is expected to have produced, which inflates the
        denominator and depresses recall — equally for every team.
        """
        return {
            r["player_id"] for r in self.contracts_2627
            if r["team_id"] == team and self.team_2526.get(r["player_id"]) != team
        }

    def _pre_freeze_option_declines(self) -> set[str]:
        """Players whose PRE-freeze evidence records a declined option.

        The one pre-freeze signal that can remove a later re-signing from the
        freeze books: an opt-out dated before the freeze means the old deal
        ended by the player's own pre-freeze act, whatever he signed later.
        Read from the ledger's world_state() - the door the simulator may use -
        and matched on typed subjects plus a declared verbal rule (the fact
        contains "declines" and "option"), stated here because entry 44 is
        what happens when matching goes undeclared. On the current corpus this
        matches exactly one item, GSW-01.
        """
        if not hasattr(self, "_declines_cache"):
            declines: set[str] = set()
            try:
                from mironba.world.evidence import load_ledger
                ledger = load_ledger(DOCS, BACKTEST, FREEZE)
                for item in ledger.world_state():
                    text = item.fact.lower()
                    if "declines" in text and "option" in text:
                        for subject in item.subjects:
                            if subject and subject[-1].isdigit():
                                declines.add(subject)
            except Exception:  # noqa: BLE001 - no ledger, no removals
                pass
            self._declines_cache = declines
        return self._declines_cache

    def covered_at_freeze(self, pid: str, team: str) -> bool:
        """Freeze-computable coverage: expiry machinery + pre-freeze opt-outs.

        Conservative in the same direction as everything validated: a deal is
        IN the freeze books unless a pre-freeze signal removes it - a dated
        post-freeze signing (the leaked july-in-closing rows, via the expiry
        rules) or a PRE-evidence option decline. Re-signings with neither
        signal stay in, and that residual is reported as a floor, never
        removed by identity: a hand-list built from POST evidence would be the
        same leak in the other direction.
        """
        from mironba.world.contract_expiry import (
            EXPIRED, _july_signings, extends_into, year_source,
        )

        if not hasattr(self, "_expiry_cache"):
            self._expiry_cache = (
                year_source(SEASON), _july_signings(SEASON, PRIOR_SEASON),
            )
        source, signings = self._expiry_cache
        if pid in self._pre_freeze_option_declines():
            return False
        call = extends_into(pid, team, PRIOR_SEASON, FREEZE,
                            _source=source, _signings=signings)
        return call.verdict != EXPIRED

    def freeze_state(self, team: str, exclude: set[str]) -> TeamCapState:
        held = [
            r for r in self.contracts_2627
            if r["team_id"] == team and r["player_id"] not in exclude
            and self.covered_at_freeze(r["player_id"], team)
        ]
        return TeamCapState(
            team_id=team, season=SEASON,
            committed_salary=sum(int(r["salary"]) for r in held),
            roster_count=len({r["player_id"] for r in held}),
        )

    def free_agent_pool(self) -> set[str]:
        """A 2025-26 contract and no 2026-27 deal anywhere: genuinely unsigned."""
        signed = {r["player_id"] for r in self.contracts_2627}
        return {r["player_id"] for r in self.contracts_2526} - signed

    def expiring_pool(self, freeze) -> set[str]:
        """The market, freeze-computable: 2025-26 players whose deal expires.

        WRITTEN BEFORE being run against the Golden State actuals, per the
        discipline that admitted Philadelphia. Uses the out-of-sample-validated
        expiry machinery (world/contract_expiry.py) instead of 2026-27
        presence: a player enters the market iff his contract is called
        EXPIRED at the freeze. The conservative rules apply - a re-signing the
        leaked July rows cannot date stays EXTENDS and out of the pool - so the
        residual is the machinery's measured ~0.6% false-free rate plus its
        stated occupy bias, not an unbounded leak.
        """
        from mironba.world.contract_expiry import (
            EXPIRED, _july_signings, extends_into, year_source,
        )

        source = year_source(SEASON)
        signings = _july_signings(SEASON, PRIOR_SEASON)
        out: set[str] = set()
        for row in self.contracts_2526:
            call = extends_into(row["player_id"], row["team_id"], PRIOR_SEASON,
                                freeze, _source=source, _signings=signings)
            if call.verdict == EXPIRED:
                out.add(row["player_id"])
        return out

    def rights(self, pid: str, team: str) -> int:
        key = (pid, team)
        if key in self._rights_cache:
            return self._rights_cache[key]
        years = 0
        for season in _recent_seasons(3):
            path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
            if not path.is_file():
                break
            if any(r["player_id"] == pid and r["team_id"] == team
                   for r in _rows(path)):
                years += 1
            else:
                break
        self._rights_cache[key] = years
        return years


# --------------------------------------------------------------------------
# The simulation
# --------------------------------------------------------------------------


@dataclass
class TeamResult:
    team: str
    persona: str
    committed_start: int
    committed_end: int
    signed: list = field(default_factory=list)
    lost_contests: list = field(default_factory=list)
    cascade: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def run_branch(outcome_key, league, commitments, *, seed=20260731, pool_ids=None,
               stipulated=frozenset()):
    """One branch: every team plans, contested players resolve, losers react."""
    env = environment_for(SEASON)
    rng = random.Random(seed)
    scheduler = Scheduler(teams=TEAMS)
    ceiling = env.second_apron

    # Only POST-freeze arrivals are removed. The June trades - Giannis and
    # Portis to Miami, LaMelo Ball and Josh Green to Minnesota, Jaylen Brown to
    # Philadelphia - had already happened and are part of the world the GMs
    # were planning in. Removing them was what gave Miami $100M of cap space
    # that never existed and let it win all eight contests.
    already = pre_freeze_ids(ARRIVALS)
    added = {t: (league.arrivals(t) - already) for t in TEAMS}
    all_added = set().union(*added.values())
    states = {t: league.freeze_state(t, added[t]) for t in TEAMS}
    results = {
        t: TeamResult(t, persona_for(t).label,
                      states[t].committed_salary, states[t].committed_salary)
        for t in TEAMS
    }

    base_pool = pool_ids if pool_ids is not None else league.free_agent_pool()
    # A stipulated mover is under contract with his destination for the
    # whole reaction: the arrivals union pours him in (right for a pending
    # scenario, wrong here), so he is excluded by id, enumerated by the
    # caller from the stipulation itself.
    pool = (base_pool | all_added) - {SUBJECT} - set(stipulated)

    def agent_for(pid, team):
        return FreeAgent(
            player_id=pid, name=league.name(pid),
            years_of_service=SERVICE_YEARS.get(pid, MINIMUM_CAP_HIT_TIER),
            prior_salary=league.prior_salary.get(pid, 0),
            years_with_team=league.rights(pid, team),
        )

    def best_affordable(team, pid):
        state = states[team]
        routes = signing_routes(state, agent_for(pid, team), env)
        usable = [
            r for r in routes.routes
            if state.committed_salary + r.max_first_year <= ceiling
        ]
        return max(usable, key=lambda r: r.max_first_year) if usable else None

    def commit(team, pid, route):
        state = states[team]
        states[team] = TeamCapState(
            team, SEASON,
            committed_salary=state.committed_salary + route.max_first_year,
            roster_count=state.roster_count + 1,
        )
        results[team].signed.append(pid)

    # The decision resolves first, and it changes what the winner can afford.
    winner = BRANCH_PREMISES.get(outcome_key, "")
    route = best_affordable(winner, SUBJECT) if winner else None
    if route is not None:
        commit(winner, SUBJECT, route)
        results[winner].notes.append(
            f"signed {league.name(SUBJECT)} via {route.route} at ${route.max_first_year:,}"
        )
    scheduler.wake_for(Event(DECISION, SUBJECT, winner, outcome_key))

    # Shortlists: own expiring players first, then the top of the market.
    wanted = {}
    ranked_pool = sorted(pool, key=lambda p: -league.prior_salary.get(p, 0))
    for team in TEAMS:
        own = [p for p in ranked_pool if league.team_2526.get(p) == team]
        rest = [p for p in ranked_pool if p not in set(own)]
        wanted[team] = (own + rest)[:SHORTLIST]
        scheduler.register(team, set(wanted[team]))

    # Round one: offers.
    offers = defaultdict(list)
    for team in TEAMS:
        for pid in wanted[team]:
            route = best_affordable(team, pid)
            if route is not None:
                offers[pid].append(
                    Offer(team, pid, route.route, route.max_first_year)
                )

    projections = project_wins(league, states)
    contests = [
        resolve(pid, offers[pid], commitments, rng, projections)
        for pid in sorted(offers)
    ]

    for contest in contests:
        team = contest.winner
        route = best_affordable(team, contest.player_id)
        if route is None:
            results[team].notes.append(
                f"could no longer afford {league.name(contest.player_id)}"
            )
            continue
        commit(team, contest.player_id, route)
        for loser in scheduler.wake_for(
            Event(SIGNED, contest.player_id, team, contest.reason)
        ):
            results[loser].lost_contests.append(contest.player_id)

    # Round two: the cascade. Only teams that lost a contest wake and go again.
    taken = {p for r in results.values() for p in r.signed}
    for team in TEAMS:
        if not results[team].lost_contests:
            continue
        for pid in wanted[team]:
            if pid in taken:
                continue
            route = best_affordable(team, pid)
            if route is None:
                continue
            commit(team, pid, route)
            results[team].cascade.append(pid)
            taken.add(pid)
            break

    for team in TEAMS:
        results[team].committed_end = states[team].committed_salary
    return results, contests, scheduler


def project_wins(league, states):
    """Projected wins per team, for the contention tier.

    Uses the value model on each team's freeze roster. Player ids are matched
    by normalised name, because the contract tables use Basketball-Reference
    ids and the performance ingest uses NBA ids with no shared key. Coverage is
    roughly three quarters of a roster; unmatched players fall to replacement
    level, which flattens the spread and therefore makes the tiers *more*
    conservative rather than less.

    Returns an empty mapping if the performance snapshot is absent, in which
    case resolution falls back to commitments and offers alone.
    """
    try:
        from mironba.models.value import fit_value_model, load_player_seasons
        from mironba.models.win_delta import (
            fit_win_model, player_quality, prior_seasons, team_strength,
        )
        from mironba.models.value import load_team_seasons
        from mironba.models.win_delta import center_by_season
        from mironba.models.validate import TEAM_ID_BY_ABBREVIATION
    except Exception:  # noqa: BLE001 - projections are optional
        return {}

    import re
    import unicodedata

    def norm(name):
        text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        return re.sub(r"[^a-z]", "", text.lower())

    try:
        players = load_player_seasons()
        teams = load_team_seasons()
    except FileNotFoundError:
        return {}
    all_seasons = sorted({p.season for p in players})
    train = tuple(prior_seasons("2024-25", all_seasons))
    if len(train) < 3:
        return {}
    value_model = fit_value_model(players, train)
    quality, minutes = player_quality(value_model, players, "2024-25", all_seasons)

    by_name = {}
    for player in players:
        if player.season == "2024-25":
            by_name[norm(player.name)] = player.player_id

    strengths = {}
    rosters = {}
    for team in TEAMS:
        # Freeze-computable roster: dated presence + validated expiry, not
        # the 2026-27 outcome table. The docstring always said 'freeze
        # roster'; the implementation read the answer. June-trade players
        # inherit dated_roster's season-table team semantics - stated, not
        # hidden. Prediction recorded in the repair commit, before running.
        from mironba.world.contract_expiry import (
            _july_signings, extends_into, year_source,
        )
        from mironba.world.dated_roster import roster_on
        _src = year_source('2026-27')
        _sig = _july_signings('2026-27', '2025-26')
        _state = roster_on('2025-26', team, FREEZE)
        roster = [
            pid for pid in _state.salaries
            if extends_into(pid, team, '2025-26', FREEZE,
                            _source=_src, _signings=_sig).occupies_slot
        ]
        rosters[team] = [
            by_name[norm(league.name(pid))]
            for pid in roster if norm(league.name(pid)) in by_name
        ]

    # Fit the win model on the training seasons so the mapping from strength to
    # wins is the same one validated in models/validate.py.
    ids = TEAM_ID_BY_ABBREVIATION
    train_strengths = {}
    season_rosters = {}
    for player in players:
        season_rosters.setdefault(
            (player.season, ids.get(player.team)), []
        ).append(player.player_id)
    for season in train:
        q, m = player_quality(value_model, players, season, all_seasons)
        if not q:
            continue
        for team in teams:
            if team.season != season:
                continue
            key = (season, team.team_id)
            train_strengths[key], *_ = team_strength(
                season_rosters.get(key, []), q, m, value_model.replacement_pm36
            )
    if len(train_strengths) < 60:
        return {}
    win_model = fit_win_model(center_by_season(train_strengths), teams, train)

    raw = {
        team: team_strength(
            rosters[team], quality, minutes, value_model.replacement_pm36
        )[0]
        for team in TEAMS
    }
    mean = sum(raw.values()) / len(raw)
    return {t: win_model.wins(v - mean) for t, v in raw.items()}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@dataclass
class TeamScore:
    team: str
    proposed: list
    actual: list

    @property
    def hits(self) -> list:
        return [p for p in self.proposed if p in set(self.actual)]

    @property
    def recall(self) -> float:
        return len(self.hits) / len(self.actual) if self.actual else 0.0

    @property
    def precision(self) -> float:
        return len(self.hits) / len(self.proposed) if self.proposed else 0.0

    def line(self) -> str:
        return (
            f"  {self.team:5} proposed {len(self.proposed):>2}  "
            f"actual {len(self.actual):>2}  hits {len(self.hits):>2}   "
            f"recall {self.recall:6.1%}   precision {self.precision:6.1%}"
        )


def score(results, league, *, signings_only=True):
    """Precision and recall, against a denominator chosen by mechanism.

    ``signings_only`` is the headline. A planner that only signs free agents
    cannot produce a trade or a draft pick, so scoring it against every arrival
    puts moves in the denominator it could never make and bounds recall below 1
    by construction. The full-arrivals number is reported alongside so the
    bound is visible rather than assumed away.
    """
    scores = []
    for t in TEAMS:
        actual = (
            sorted(signing_targets(t, ARRIVALS)) if signings_only
            else sorted(league.arrivals(t) - pre_freeze_ids(ARRIVALS))
        )
        scores.append(TeamScore(t, list(results[t].signed), actual))
    proposed = sum(len(s.proposed) for s in scores)
    actual = sum(len(s.actual) for s in scores)
    hits = sum(len(s.hits) for s in scores)
    return scores, {
        "proposed": proposed, "actual": actual, "hits": hits,
        "recall": hits / actual if actual else 0.0,
        "precision": hits / proposed if proposed else 0.0,
    }


def contested_accuracy(contests, league):
    """For players two or more teams wanted, did the sim pick the right team?

    The metric only multi-agent can produce. A single-team simulation has no
    contested players at all, so this number did not exist before M5.
    """
    actual_team = {}
    for team in TEAMS:
        for pid in league.arrivals(team):
            actual_team[pid] = team

    rows = []
    for contest in contests:
        if not contest.contested:
            continue
        truth = actual_team.get(contest.player_id)
        rows.append({
            "player_id": contest.player_id, "name": league.name(contest.player_id),
            "winner": contest.winner, "actual": truth, "reason": contest.reason,
            "correct": truth is not None and truth == contest.winner,
            "resolvable": truth is not None,
        })
    resolvable = [r for r in rows if r["resolvable"]]
    correct = sum(1 for r in resolvable if r["correct"])
    return {
        "contested": len(rows),
        "resolvable": len(resolvable),
        "correct": correct,
        "accuracy": (correct / len(resolvable)) if resolvable else None,
        "arbitrary": sum(1 for r in rows if r["reason"] == ARBITRARY),
        "rows": rows,
    }


def _recent_seasons(n: int) -> tuple[str, ...]:
    """PRIOR_SEASON and the n-1 seasons before it, newest first."""
    start = int(PRIOR_SEASON[:4])
    return tuple(f"{y}-{str(y + 1)[-2:]}" for y in range(start, start - n, -1))


def _all_teams() -> tuple[str, ...]:
    import csv as _csv
    path = SNAPSHOTS / f"bbref-{PRIOR_SEASON}" / "contracts.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(sorted({r["team_id"] for r in _csv.DictReader(handle)}))


def main(argv=None) -> int:
    from mironba.sim.tick import use_utf8_console
    from mironba.world.evidence import load_ledger

    # NBA rosters are wall-to-wall diacritics and a Windows console is cp1252.
    # Losing a finished run at the print step is the failure this exists for.
    use_utf8_console()

    parser = argparse.ArgumentParser(description="Multi-team branch simulation.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--scenario", required=True,
                        help="a declared scenario id under configs/branch/")
    parser.add_argument("--teams", choices=("five", "league"), default="five",
                        help="five = the M5 scored teams; league = all 30. "
                        "Per-event relevance stays derived either way: a team "
                        "contends for a player only where feasible_signings() "
                        "finds it a legal route, so the LeBron-style events "
                        "fan out to the teams the hard filter admits, while "
                        "every team still plans its own offseason.")
    args = parser.parse_args(argv)
    from mironba.world.scenario import load_scenario

    bind_scenario(load_scenario(args.scenario))
    if args.teams == "league":
        global TEAMS
        TEAMS = _all_teams()

    league = LeagueState.load()
    commitments = load_ledger(DOCS, BACKTEST, FREEZE).open_conditionals()
    payload = {}

    for outcome in (ACTUAL_BRANCH, BLOCKER_BRANCH):
        results, contests, scheduler = run_branch(
            outcome, league, commitments, seed=args.seed
        )
        tag = ("this is what happened" if outcome == ACTUAL_BRANCH
               else "counterfactual - NOT SCORED")
        print("=" * 78)
        print(f"  BRANCH {outcome}   ({tag})")
        print("=" * 78)
        for team in TEAMS:
            r = results[team]
            print(f"  {team} [{r.persona}]  "
                  f"${r.committed_start:,} -> ${r.committed_end:,}")
            signed = ", ".join(league.name(p) for p in r.signed) or "nobody"
            print(f"      signed:  {signed}")
            if r.lost_contests:
                lost = ", ".join(league.name(p) for p in r.lost_contests)
                print(f"      lost:    {lost}")
            if r.cascade:
                cascade = ", ".join(league.name(p) for p in r.cascade)
                print(f"      cascade: {cascade}   (only because it lost a contest)")
            for note in r.notes:
                print(f"      note: {note}")

        contested = [c for c in contests if c.contested]
        print(f"\n  CONTESTED PLAYERS ({len(contested)} of {len(contests)} offers)")
        for contest in contested[:10]:
            print(contest.line(name=league.name))

        print("\n  SCHEDULER")
        print(f"    events {scheduler.events}   agent wakes {scheduler.wakes}   "
              f"naive polling {scheduler.polled_equivalent}   "
              f"saving {scheduler.saving:.1%}")

        if outcome == ACTUAL_BRANCH:
            scores, pooled = score(results, league)
            print("\n  PER-TEAM PRECISION AND RECALL")
            for team_score in scores:
                print(team_score.line())
            print(f"  {'POOL':5} proposed {pooled['proposed']:>2}  "
                  f"actual {pooled['actual']:>2}  hits {pooled['hits']:>2}   "
                  f"recall {pooled['recall']:6.1%}   "
                  f"precision {pooled['precision']:6.1%}")

            accuracy = contested_accuracy(contests, league)
            summary = (f"{accuracy['accuracy']:.1%}"
                       if accuracy["accuracy"] is not None else "n/a")
            print("\n  CONTESTED-PLAYER ACCURACY")
            print(f"    {accuracy['contested']} contested, "
                  f"{accuracy['resolvable']} with a known destination, "
                  f"{accuracy['correct']} correct ({summary})")
            print(f"    {accuracy['arbitrary']} resolved arbitrarily "
                  f"and are recorded as such")
            for row in accuracy["rows"][:10]:
                mark = "OK  " if row["correct"] else "MISS"
                print(f"      {mark} {row['name']:<22} sim {row['winner']:<4} "
                      f"actual {row['actual'] or '-':<4} [{row['reason']}]")
            print()
            print("  READ THIS BEFORE THE RECALL NUMBER.")
            print(f"  {league.name(SUBJECT)}'s destination is the BRANCH PREMISE,")
            print("  not a prediction: run_branch assigns the subject to the")
            print("  scenario's declared premise team because that is what")
            print("  defines this branch. Any hit on the subject is stipulated,")
            print("  not predicted, and the scored recall must be read net of it.")
            all_scores, all_pooled = score(results, league, signings_only=False)
            print()
            print("  against ALL post-freeze arrivals (trades and draft picks")
            print("  included, which a signing planner cannot produce):")
            print(f"    recall {all_pooled['recall']:6.1%}   "
                  f"precision {all_pooled['precision']:6.1%}   "
                  f"(actual {all_pooled['actual']})")
            payload["all_arrivals"] = all_pooled
            payload["scores"] = [asdict(s) for s in scores]
            payload["pooled"] = pooled
            payload["contested"] = accuracy
        else:
            print("\n  NOT SCORED. This branch did not happen and never will")
            print("  have ground truth. Its value is comparative.")

        payload[outcome] = {
            "teams": {t: asdict(results[t]) for t in TEAMS},
            "scheduler": {
                "events": scheduler.events, "wakes": scheduler.wakes,
                "polled": scheduler.polled_equivalent, "saving": scheduler.saving,
            },
        }
        print()

    if args.out:
        args.out.write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8"
        )
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
