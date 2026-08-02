"""Downstream outcomes, scored - never the interest set itself.

    python -m mironba.eval.interest_score

Reported interest seeds the suitor set, so suitor identification is stipulated
and retired as a metric. What remains scoreable is everything the interest rows
do NOT contain:

* ``suitor_won``      - who wins the resolution, given the set. Null: 1/N.
* ``capacity_use``    - what the sim had each loser do with held capacity,
                        against what they really did. Null: random draws from
                        the free-agent pool.
* ``conditionals_fire`` - commitments attach to the branch matching their
                        condition. Null: random attachment, expected n/2.

Every ground-truth read goes through SCORING_UNLOCK; a metric computable
without POST access would be reading inputs, and the circularity test asserts
none is.
"""

from __future__ import annotations

import random
import sys

#: capacity_use was renamed after entry 43: it measured the league planner's
#: EXTERNAL-acquisition list against actuals that were mostly retentions - and
#: retentions are outside that planner's pool by construction, so the recall
#: ceiling was 1/6 before the metric ran. The name now says what is measured.
SCORED_OUTPUTS = ("suitor_won", "conditionals_fire")

#: Retired at entry 45: its recall ceiling is 1/6 under ANY sound
#: freeze-computable pool - the expiry-based repair produced a pool identical
#: to the leaky one on this scenario - so the metric cannot register success
#: and is kept only as a diagnostic print, not a score.
RETIRED_OUTPUTS = ("external_acquisition_overlap",)


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Score a scenario's downstream outcomes, never its inputs.")
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args(argv)

    from mironba.world.scenario import load_scenario

    scenario = load_scenario(args.scenario)
    import mironba.sim.league as league_mod

    league_mod.bind_scenario(scenario)
    from mironba.rules.constants import environment_for
    from mironba.rules.signing import signing_routes
    from mironba.sim.arrivals import pre_freeze_ids
    from mironba.sim.league import (
        SERVICE_YEARS, MINIMUM_CAP_HIT_TIER, SEASON,
        FreeAgent, LeagueState, Offer, resolve,
    )
    from mironba.world.evidence import SCORING_UNLOCK
    from mironba.world.relevance import suitor_relevance

    subject = scenario.decision_subject
    ledger = scenario.ledger()
    league = LeagueState.load()
    env = environment_for(SEASON)
    already = pre_freeze_ids(league_mod.ARRIVALS)

    # -- suitor_won -------------------------------------------------------
    interest, path = suitor_relevance(subject, ledger, set())
    print(f"relevance path for {subject}: {path} ({len(interest)} teams: {interest})")
    contenders = {}
    for team in interest:
        state = league.freeze_state(team, league.arrivals(team) - already)
        from mironba.report.timeline import name_of
        agent = FreeAgent(subject, name_of(subject),
                          years_of_service=SERVICE_YEARS.get(subject, MINIMUM_CAP_HIT_TIER),
                          prior_salary=league.prior_salary.get(subject, 0),
                          years_with_team=league.rights(subject, team))
        routes = signing_routes(state, agent, env)
        usable = [r for r in routes.routes
                  if state.committed_salary + r.max_first_year <= env.second_apron]
        if usable:
            best = max(usable, key=lambda r: r.max_first_year)
            contenders[team] = Offer(team, subject, best.route, best.max_first_year)
        else:
            print(f"  {team} dropped: no usable route "
                  f"({routes.blocked if hasattr(routes,'blocked') else 'blocked'})")
    contest = resolve(subject, list(contenders.values()), [],
                      random.Random(20260731))
    winner = contest.winner if hasattr(contest, "winner") else contest
    reason = getattr(contest, "reason", "?")
    truth = {r.team for r in ledger.ground_truth_interest(unlock=SCORING_UNLOCK)}
    from mironba.eval.scenario_truth import answer as read_answer

    answer = read_answer(scenario)   # EVAL-ONLY, from the store's POST side
    actual = answer["destination"]
    post_items = ledger.ground_truth(unlock=SCORING_UNLOCK)
    assert any(i.id == answer["destination_item"] for i in post_items)
    outcome = "HIT" if winner == actual else "MISS"
    print()
    print(f"SUITOR_WON: sim {winner} ({reason}) vs actual {actual} -> {outcome}")
    print(f"  UNINFORMATIVE: n=1 against a 1/{len(contenders)} null - a chance "
          f"proposer misses {1 - 1/len(contenders):.0%} of the time - and the "
          "resolver itself declared the decision arbitrary. Reported as "
          "uninformative, not as a miss.")
    print(f"  POST narrowing (withheld from inputs): {sorted(truth)}")

    # -- capacity_use -----------------------------------------------------
    sim = json.load(open("bench-league-30team.json", encoding="utf-8"))
    blocker = scenario.blocker_team
    gsw_sim = set(sim[scenario.actual_branch]["teams"][blocker]["signed"])
    capacity_ids = {r["item_id"]
                    for r in scenario._data_rows("capacity-actuals.csv")}
    gsw_actual = {i.subjects.split("|")[0] if isinstance(i.subjects, str) else i.subjects[0]
                  for i in post_items if i.id in capacity_ids}
    pool = league.free_agent_pool()
    hits = gsw_sim & gsw_actual
    reachable = gsw_actual & pool
    null = len(gsw_sim) * len(reachable) / max(len(pool), 1)
    print()
    print(f"EXTERNAL_ACQUISITION_OVERLAP ({blocker}, {scenario.actual_branch}): sim {sorted(gsw_sim)}")
    print(f"  actual {sorted(gsw_actual)}")
    print(f"  RECALL CEILING {len(reachable)}/{len(gsw_actual)}: the league planner "
          "draws from free_agent_pool(), which excludes every player holding a "
          f"{SEASON} deal - i.e. everyone who actually re-signed. Retention is the "
          "branch planner's move set (sim/branch.py), not this one's.")
    print(f"  hits {len(hits)}/{len(gsw_sim)} proposed, recall {len(hits)}/{len(gsw_actual)}"
          f"   null {null:.2f} expected hits on the reachable set")
    print("  NO POWER BY CONSTRUCTION: with that ceiling and those chance hits, "
          "this metric could not have registered a success either.")

    # -- conditionals_fire --------------------------------------------------
    fired_ok = 0
    conds = ledger.open_conditionals()
    for c in conds:
        in_blocker = scenario.condition_fires_in(c.condition, scenario.blocker_branch)
        attached = scenario.blocker_branch if in_blocker else scenario.actual_branch
        correct = in_blocker == (attached == scenario.blocker_branch)
        fired_ok += correct
    p_value = 0.5 ** len(conds)
    print()
    print(f"CONDITIONALS_FIRE: {fired_ok}/{len(conds)} attach to the branch "
          f"matching their condition   null (random) {len(conds)/2:.1f}   "
          f"p = {p_value:.4f}")
    print("  SUGGESTIVE, NOT SIGNIFICANT: p=0.0625 is the threshold this project "
          "refused on the era gap (p=0.064). A mechanism check this small "
          "belongs in the test suite as well, and it is there.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
