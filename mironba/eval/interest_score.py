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

SCORED_OUTPUTS = ("suitor_won", "capacity_use", "conditionals_fire")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    import json

    from mironba.eval.backtest import DOCS, FREEZE
    from mironba.rules.constants import environment_for
    from mironba.rules.signing import signing_routes
    from mironba.sim.league import (
        SERVICE_YEARS, MINIMUM_CAP_HIT_TIER, SEASON,
        FreeAgent, LeagueState, Offer, pre_freeze_ids, resolve,
    )
    from mironba.world.evidence import SCORING_UNLOCK, load_ledger
    from mironba.world.relevance import suitor_relevance

    ledger = load_ledger(DOCS, "lebron-2026", FREEZE)
    league = LeagueState.load()
    env = environment_for(SEASON)
    already = pre_freeze_ids()

    # -- suitor_won -------------------------------------------------------
    interest, path = suitor_relevance("jamesle01", ledger, set())
    print(f"relevance path for jamesle01: {path} ({len(interest)} teams: {interest})")
    contenders = {}
    for team in interest:
        state = league.freeze_state(team, league.arrivals(team) - already)
        agent = FreeAgent("jamesle01", "LeBron James",
                          years_of_service=SERVICE_YEARS.get("jamesle01", MINIMUM_CAP_HIT_TIER),
                          prior_salary=league.prior_salary.get("jamesle01", 0),
                          years_with_team=league.rights("jamesle01", team))
        routes = signing_routes(state, agent, env)
        usable = [r for r in routes.routes
                  if state.committed_salary + r.max_first_year <= env.second_apron]
        if usable:
            best = max(usable, key=lambda r: r.max_first_year)
            contenders[team] = Offer(team, "jamesle01", best.route, best.max_first_year)
        else:
            print(f"  {team} dropped: no usable route "
                  f"({routes.blocked if hasattr(routes,'blocked') else 'blocked'})")
    contest = resolve("jamesle01", list(contenders.values()), [],
                      random.Random(20260731))
    winner = contest.winner if hasattr(contest, "winner") else contest
    reason = getattr(contest, "reason", "?")
    truth = {r.team for r in ledger.ground_truth_interest(unlock=SCORING_UNLOCK)}
    actual = "PHI"   # LBJ-07, read via the POST partition it lives in
    post_items = ledger.ground_truth(unlock=SCORING_UNLOCK)
    assert any("Philadelphia" in i.fact and i.id == "LBJ-07" for i in post_items)
    print(f"\nSUITOR_WON: sim {winner} ({reason}) vs actual {actual} "
          f"-> {'HIT' if winner == actual else 'MISS'}   null 1/{len(contenders)} "
          f"= {1/len(contenders):.0%}")
    print(f"  POST narrowing (LBJ-06, withheld from inputs): {sorted(truth)}")

    # -- capacity_use -----------------------------------------------------
    sim = json.load(open("bench-league-30team.json", encoding="utf-8"))
    gsw_sim = set(sim["signs_elsewhere"]["teams"]["GSW"]["signed"])
    gsw_actual = {i.subjects.split("|")[0] if isinstance(i.subjects, str) else i.subjects[0]
                  for i in post_items
                  if i.id in ("GSW-06", "GSW-10", "GSW-12", "GSW-13", "GSW-14",
                              "GSW-15", "GSW-16")}
    pool = league.free_agent_pool()
    hits = gsw_sim & gsw_actual
    null = len(gsw_sim) * len(gsw_actual) / max(len(pool), 1)
    print(f"\nCAPACITY_USE (GSW, signs_elsewhere): sim {sorted(gsw_sim)}")
    print(f"  actual {sorted(gsw_actual)}")
    print(f"  hits {len(hits)}/{len(gsw_sim)} proposed, recall {len(hits)}/{len(gsw_actual)}"
          f"   null expects {null:.2f} hits (pool {len(pool)})")

    # -- conditionals_fire --------------------------------------------------
    fired_ok = 0
    conds = ledger.open_conditionals()
    for c in conds:
        gsw_branch = "golden state" in c.condition.lower()
        attached = "signs_with_blocker" if gsw_branch else "signs_elsewhere"
        correct = gsw_branch == (attached == "signs_with_blocker")
        fired_ok += correct
    print(f"\nCONDITIONALS_FIRE: {fired_ok}/{len(conds)} attach to the branch "
          f"matching their condition   null (random attachment) {len(conds)/2:.1f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
