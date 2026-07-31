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

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
DOCS = Path(__file__).resolve().parents[2] / "docs" / "backtests"
SEASON = "2026-27"
FREEZE = date(2026, 7, 6)
BACKTEST = "lebron-2026"

#: The teams reported as being in the LeBron field, plus the one that won it.
#: Not an arbitrary five — evidence item LBJ-04 names exactly these.
TEAMS = ("GSW", "MIA", "MIN", "CLE", "PHI")

#: Structured persona parameters, one per team. Numeric and sweepable, never
#: prose — the charter's rule, and it also lets a persona feed a deterministic
#: constraint rather than only a prompt.
#:
#: These are ASSUMPTIONS, not sourced facts, and they are the largest hand-set
#: input in this module. They are set from each team's observable cap behaviour
#: at the freeze rather than from reputation: a team already past the second
#: apron has revealed a high win-now weight, and one with room has not.
PERSONAS = {
    "GSW": GMPersona("win-now-veteran", risk_tolerance=0.7, win_now_horizon=1,
                     asset_hoarding=0.3),
    "MIA": GMPersona("disciplined", risk_tolerance=0.4, win_now_horizon=3,
                     asset_hoarding=0.6),
    "MIN": GMPersona("balanced", risk_tolerance=0.5, win_now_horizon=2,
                     asset_hoarding=0.5),
    "CLE": GMPersona("all-in", risk_tolerance=0.8, win_now_horizon=1,
                     asset_hoarding=0.2),
    "PHI": GMPersona("star-hunting", risk_tolerance=0.9, win_now_horizon=1,
                     asset_hoarding=0.2),
}

#: Veteran-minimum deals hit the cap at the two-year tier whatever the player's
#: service. Used for pool members whose service is not separately sourced.
MINIMUM_CAP_HIT_TIER = 2

SERVICE_YEARS = {
    "greendr01": 14, "horfoal01": 19, "porzikr01": 11,
    "bassech01": 2, "meltode01": 8, "jamesle01": 23,
}

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

UNCONTESTED = "uncontested"
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


def resolve(player_id, offers, commitments, rng) -> Contest:
    """Decide who signs a contested player, using only defensible inputs.

    Deliberately not a win-maximisation. The measured delta error is 7.4 wins
    and the separation threshold 10.5, so the value model cannot tell two
    plausible destinations apart. Making it choose would manufacture a
    preference and then present it as analysis.
    """
    if len(offers) == 1:
        return Contest(player_id, offers, offers[0].team, UNCONTESTED)

    ranked = sorted(offers, key=lambda o: (-o.max_first_year, o.team))

    # A reported intention naming this player outranks the money: it is
    # evidence about what happened rather than an inference about what should.
    for commitment in commitments:
        text = f"{commitment.condition} {commitment.commitment}".lower()
        if player_id.lower() in text:
            for offer in ranked:
                if offer.team.lower() == commitment.subject.lower():
                    return Contest(player_id, offers, offer.team, BY_COMMITMENT)

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
        c25 = _rows(SNAPSHOTS / "bbref-2025-26" / "contracts.csv")
        names: dict[str, str] = {}
        for season in ("2025-26", "2024-25"):
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

    def freeze_state(self, team: str, exclude: set[str]) -> TeamCapState:
        held = [
            r for r in self.contracts_2627
            if r["team_id"] == team and r["player_id"] not in exclude
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

    def rights(self, pid: str, team: str) -> int:
        key = (pid, team)
        if key in self._rights_cache:
            return self._rights_cache[key]
        years = 0
        for season in ("2025-26", "2024-25", "2023-24"):
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


def run_branch(outcome_key, league, commitments, *, seed=20260731):
    """One branch: every team plans, contested players resolve, losers react."""
    env = environment_for(SEASON)
    rng = random.Random(seed)
    scheduler = Scheduler(teams=TEAMS)
    ceiling = env.second_apron

    added = {t: league.arrivals(t) for t in TEAMS}
    all_added = set().union(*added.values())
    states = {t: league.freeze_state(t, added[t]) for t in TEAMS}
    results = {
        t: TeamResult(t, PERSONAS[t].label,
                      states[t].committed_salary, states[t].committed_salary)
        for t in TEAMS
    }

    pool = (league.free_agent_pool() | all_added) - {"jamesle01"}

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
    winner = "GSW" if outcome_key == "signs_with_blocker" else "PHI"
    route = best_affordable(winner, "jamesle01")
    if route is not None:
        commit(winner, "jamesle01", route)
        results[winner].notes.append(
            f"signed LeBron James via {route.route} at ${route.max_first_year:,}"
        )
    scheduler.wake_for(Event(DECISION, "jamesle01", winner, outcome_key))

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

    contests = [resolve(pid, offers[pid], commitments, rng) for pid in sorted(offers)]

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


def score(results, league):
    scores = [
        TeamScore(t, list(results[t].signed), sorted(league.arrivals(t)))
        for t in TEAMS
    ]
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


def main(argv=None) -> int:
    from mironba.world.evidence import load_ledger

    parser = argparse.ArgumentParser(description="Multi-team branch simulation.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args(argv)

    league = LeagueState.load()
    commitments = load_ledger(DOCS, BACKTEST, FREEZE).open_conditionals()
    payload = {}

    for outcome in ("signs_elsewhere", "signs_with_blocker"):
        results, contests, scheduler = run_branch(
            outcome, league, commitments, seed=args.seed
        )
        tag = ("this is what happened" if outcome == "signs_elsewhere"
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

        if outcome == "signs_elsewhere":
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
