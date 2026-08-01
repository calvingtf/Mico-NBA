"""Deadline backtest pooled across every season with standings coverage.

    python -m mironba.eval.pooled_backtest

Every figure is reported with the value a random proposer scores on the same
data, and the delta. A number without its null does not go in the README.
"""

from __future__ import annotations

import json
import sys
from itertools import combinations
from math import comb
from pathlib import Path

PAIR_SPACE = 435


def p_hit(qualifying: int, drawn: int, space: int = PAIR_SPACE) -> float:
    if space - qualifying < drawn:
        return 1.0
    return 1 - comb(space - qualifying, drawn) / comb(space, drawn)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from mironba.sim.deadline import actual_deadline_trades, run, score
    from mironba.world.calendar import CALENDARS

    total = {"proposed": 0, "actual": 0, "matched": 0, "hits": 0}
    nulls: list[float] = []
    qualifying: set[frozenset] = set()
    rows = []
    for season in sorted(CALENDARS):
        result = run(season=season)
        scored = score(result, season=season)
        pairs = {frozenset(p.pair) for p in result.proposals}
        actual = actual_deadline_trades(season)
        qualifying |= {
            frozenset(c) for t in actual for c in combinations(sorted(t.teams), 2)
        }
        for trade in actual:
            q = len({frozenset(c) for c in combinations(sorted(trade.teams), 2)})
            nulls.append(p_hit(q, len(pairs)))
        rows.append({
            "season": season, "proposed": scored.proposed,
            "pairs": len(pairs), "actual": scored.actual,
            "matched": scored.actual_matched, "hits": scored.pair_hits,
        })
        # The first line is the check: proposed=0 against actual>0 means
        # something upstream is empty, and the run is not worth finishing.
        print(f"{season} proposed={scored.proposed} pairs={len(pairs)} "
              f"actual={scored.actual} matched={scored.actual_matched}", flush=True)
        if scored.proposed == 0 and scored.actual > 0:
            print(f"  STOP: {season} proposed nothing against "
                  f"{scored.actual} real trades - upstream input is empty",
                  flush=True)
            return 2
        total["proposed"] += scored.proposed
        total["actual"] += scored.actual
        total["matched"] += scored.actual_matched
        total["hits"] += scored.pair_hits

    null_precision = len(qualifying) / PAIR_SPACE * 100
    precision = total["hits"] / total["proposed"] * 100 if total["proposed"] else 0.0
    recall = total["matched"] / total["actual"] * 100 if total["actual"] else 0.0
    null_recall = sum(nulls) / total["actual"] * 100 if total["actual"] else 0.0

    print("\n=== POOLED ===")
    print(f"proposed {total['proposed']}  actual {total['actual']}  "
          f"matched {total['matched']}")
    print(f"recall     {recall:6.1f}%   null {null_recall:6.1f}%   "
          f"delta {recall - null_recall:+.1f} pts")
    print(f"precision  {precision:6.2f}%   null {null_precision:6.2f}%   "
          f"delta {precision - null_precision:+.2f} pts")
    Path("bench-pooled-10season.json").write_text(
        json.dumps({"rows": rows, "total": total, "precision": precision,
                    "null_precision": null_precision, "recall": recall,
                    "null_recall": null_recall}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
