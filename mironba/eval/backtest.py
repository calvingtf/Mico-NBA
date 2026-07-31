"""The whole LeBron 2026 backtest, end to end, in one command.

    python -m mironba.eval.backtest

Runs the freeze, both branches, the multi-team market, the scoring and the
leakage audit, and prints the result. Nothing here computes anything new — it
is a front door, so that "does this work" has an answer shorter than a tour of
six modules.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs" / "backtests"
FREEZE = date(2026, 7, 6)


def main(argv=None) -> int:
    from mironba.sim.arrivals import ARRIVALS, summary
    from mironba.sim.branch import leakage_audit
    from mironba.sim.league import (
        LeagueState, TEAMS, contested_accuracy, run_branch, score,
    )
    from mironba.sim.tick import use_utf8_console
    from mironba.world.evidence import load_ledger

    use_utf8_console()
    parser = argparse.ArgumentParser(description="Run the LeBron 2026 backtest.")
    parser.add_argument("--seed", type=int, default=20260731)
    args = parser.parse_args(argv)

    ledger = load_ledger(DOCS, "lebron-2026", FREEZE)
    league = LeagueState.load()
    commitments = ledger.open_conditionals()

    print("=" * 76)
    print("  MiroNBA - LeBron James, 2026 free agency")
    print("=" * 76)
    print(f"  freeze          {FREEZE} 16:01Z (end of the July moratorium)")
    print(f"  evidence        {len(ledger.world_state())} PRE items visible, "
          f"{len(ledger.items) - len(ledger.world_state())} POST withheld")
    print(f"  open decisions  1 (where does James sign)")
    print(f"  teams           {', '.join(TEAMS)}")
    print(f"  arrivals        {summary()}")

    print("\n" + "=" * 76)
    print("  BRANCHES")
    print("=" * 76)
    results = {}
    for outcome in ("signs_elsewhere", "signs_with_blocker"):
        team_results, contests, scheduler = run_branch(
            outcome, league, commitments, seed=args.seed
        )
        results[outcome] = (team_results, contests, scheduler)
        label = "ACTUAL" if outcome == "signs_elsewhere" else "COUNTERFACTUAL"
        print(f"\n  {outcome}  [{label}]")
        for team in TEAMS:
            r = team_results[team]
            signed = ", ".join(league.name(p) for p in r.signed) or "nobody"
            print(f"    {team}  {signed}")
        contested = [c for c in contests if c.contested]
        arbitrary = sum(1 for c in contested if "arbitrary" in c.reason)
        print(f"    {len(contested)} contested, {arbitrary} resolved arbitrarily")
        print(f"    scheduler: {scheduler.wakes} wakes vs "
              f"{scheduler.polled_equivalent} polled ({scheduler.saving:.0%} saved)")

    team_results, contests, _ = results["signs_elsewhere"]

    print("\n" + "=" * 76)
    print("  SCORED - signs_elsewhere only. The counterfactual has no ground")
    print("  truth and never will, so it is not scored.")
    print("=" * 76)
    for label, only in (("signings only (headline)", True),
                        ("all post-freeze arrivals", False)):
        _, pooled = score(team_results, league, signings_only=only)
        print(f"  {label:28} recall {pooled['recall']:6.1%}   "
              f"precision {pooled['precision']:6.1%}   "
              f"(hits {pooled['hits']} of {pooled['actual']})")
    accuracy = contested_accuracy(contests, league)
    resolved = (f"{accuracy['accuracy']:.0%}"
                if accuracy["accuracy"] is not None else "n/a")
    print(f"  {'contested-player accuracy':28} {accuracy['correct']} of "
          f"{accuracy['resolvable']} resolvable ({resolved})")
    print("\n  LeBron's destination is the branch premise, not a prediction.")
    print("  Predictive recall on non-stipulated signings is 0 of 1.")

    print("\n" + "=" * 76)
    print("  LEAKAGE AUDIT")
    print("=" * 76)
    for line in leakage_audit():
        print(line.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
