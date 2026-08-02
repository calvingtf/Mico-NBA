"""A declared scenario's backtest, end to end, in one command.

    python -m mironba.eval.backtest --scenario <id>

Runs the freeze, both branches, the multi-team market, the scoring and the
leakage audit, and prints the result. Nothing here computes anything new — it
is a front door, so that "does this work" has an answer shorter than a tour of
six modules. Everything scenario-specific comes from the named scenario file;
this module holds no identifiers of its own.
"""

from __future__ import annotations

import argparse


def main(argv=None) -> int:
    from mironba.sim.tick import use_utf8_console
    from mironba.world.scenario import load_scenario

    use_utf8_console()
    parser = argparse.ArgumentParser(
        description="Run a declared scenario's backtest end to end.")
    parser.add_argument("--scenario", required=True,
                        help="a declared scenario id under configs/branch/")
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args(argv)

    scenario = load_scenario(args.scenario)
    import mironba.sim.branch as branch_mod
    import mironba.sim.league as league_mod

    league_mod.bind_scenario(scenario)
    branch_mod.bind_scenario(scenario)
    from mironba.sim.arrivals import summary

    ledger = scenario.ledger()
    league = league_mod.LeagueState.load()
    commitments = ledger.open_conditionals()
    teams = league_mod.TEAMS

    print("=" * 76)
    print(f"  MiroNBA - {scenario.id}: {scenario.decision}")
    print("=" * 76)
    print(f"  freeze          {scenario.freeze} ({scenario.freeze_rationale})")
    print(f"  evidence        {len(ledger.world_state())} PRE items visible, "
          f"{len(ledger.items) - len(ledger.world_state())} POST withheld")
    print(f"  open decisions  1 ({scenario.decision})")
    print(f"  teams           {', '.join(teams)}")
    print(f"  arrivals        {summary(league_mod.ARRIVALS)}")

    print("\n" + "=" * 76)
    print("  BRANCHES")
    print("=" * 76)
    ordered = (scenario.actual_branch,) + tuple(
        b for b in scenario.branches if b != scenario.actual_branch
    )
    results = {}
    for outcome in ordered:
        team_results, contests, scheduler = league_mod.run_branch(
            outcome, league, commitments, seed=args.seed
        )
        results[outcome] = (team_results, contests, scheduler)
        label = "ACTUAL" if outcome == scenario.actual_branch else "COUNTERFACTUAL"
        print(f"\n  {outcome}  [{label}]")
        for team in teams:
            r = team_results[team]
            signed = ", ".join(league.name(p) for p in r.signed) or "nobody"
            print(f"    {team}  {signed}")
        contested = [c for c in contests if c.contested]
        arbitrary = sum(1 for c in contested if "arbitrary" in c.reason)
        print(f"    {len(contested)} contested, {arbitrary} resolved arbitrarily")
        print(f"    scheduler: {scheduler.wakes} wakes vs "
              f"{scheduler.polled_equivalent} polled ({scheduler.saving:.0%} saved)")

    team_results, contests, _ = results[scenario.actual_branch]

    print("\n" + "=" * 76)
    print(f"  SCORED - {scenario.actual_branch} only. The counterfactual has no")
    print("  ground truth and never will, so it is not scored.")
    print("=" * 76)
    for label, only in (("signings only (headline)", True),
                        ("all post-freeze arrivals", False)):
        _, pooled = league_mod.score(team_results, league, signings_only=only)
        print(f"  {label:28} recall {pooled['recall']:6.1%}   "
              f"precision {pooled['precision']:6.1%}   "
              f"(hits {pooled['hits']} of {pooled['actual']})")
    accuracy = league_mod.contested_accuracy(contests, league)
    resolved = (f"{accuracy['accuracy']:.0%}"
                if accuracy["accuracy"] is not None else "n/a")
    print(f"  {'contested-player accuracy':28} {accuracy['correct']} of "
          f"{accuracy['resolvable']} resolvable ({resolved})")
    print("\n  The subject's destination is the branch premise, not a prediction.")
    print("  Predictive recall on non-stipulated signings is 0 of 1.")

    print("\n" + "=" * 76)
    print("  LEAKAGE AUDIT")
    print("=" * 76)
    for line in branch_mod.leakage_audit():
        print(line.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
