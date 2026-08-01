"""Deadline backtest pooled across every season with standings coverage.

    python -m mironba.eval.pooled_backtest

Every figure is reported with the value a random proposer scores on the same
data, and the delta. A number without its null does not go in the README.
"""

from __future__ import annotations

import csv
import json
import sys
from itertools import combinations
from math import comb
from pathlib import Path

PAIR_SPACE = 435

#: Per-season results, written incrementally. A 2.5-hour run that only writes
#: on completion loses everything to a crash at season nine, and this project
#: has already lost two tables to writers that replaced instead of merging.
#: Seasons already present are kept; a re-run of one season replaces its row.
RESULTS = Path("bench-pooled-10season.csv")
FIELDS = ("season", "proposed", "pairs", "actual", "matched", "hits")


def write_season(row: dict, path: Path = RESULTS) -> None:
    """Merge one season's row into the results table.

    Partitioned by season, so it obeys the same rule as every other writer
    here: write partition B, partition A survives.
    """
    stored: dict[str, dict] = {}
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            for stored_row in csv.DictReader(handle):
                stored[stored_row["season"]] = stored_row
    stored[row["season"]] = {k: str(row[k]) for k in FIELDS}
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for season in sorted(stored):
            writer.writerow(stored[season])


def read_seasons(path: Path = RESULTS) -> dict[str, dict]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["season"]: r for r in csv.DictReader(handle)}


def pooled_null_precision(per_season: list[tuple[int, float]]) -> float:
    """Proposal-weighted mean of per-season nulls. NOT a union over seasons.

    **A proposal made in season S can only hit a trade in season S.** So the
    chance of a random proposal hitting is a per-season quantity, and pooling
    it means weighting each season's null by how many proposals that season
    contributed.

    Taking the union of qualifying pairs across seasons and dividing by 435
    instead credits the null with pairs it could never have hit: a 2016-17 pair
    is not available to a 2024-25 proposal. Over three seasons that inflated
    the null from 2.40% to 6.67% and turned an observed 2.97% from **1.24x
    chance** into "3.70 points below chance".

    ``per_season`` is ``[(proposals, null_fraction), ...]``.
    """
    total = sum(n for n, _ in per_season)
    if not total:
        return 0.0
    return sum(n * null for n, null in per_season) / total


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
    season_nulls: list[tuple[int, float]] = []
    rows = []
    for season in sorted(CALENDARS):
        result = run(season=season)
        scored = score(result, season=season)
        pairs = {frozenset(p.pair) for p in result.proposals}
        actual = actual_deadline_trades(season)
        season_pairs = {
            frozenset(c) for t in actual for c in combinations(sorted(t.teams), 2)
        }
        # Per season, and weighted at the end. Never unioned across seasons.
        season_nulls.append((scored.proposed, len(season_pairs) / PAIR_SPACE))
        for trade in actual:
            q = len({frozenset(c) for c in combinations(sorted(trade.teams), 2)})
            nulls.append(p_hit(q, len(pairs)))
        row = {
            "season": season, "proposed": scored.proposed,
            "pairs": len(pairs), "actual": scored.actual,
            "matched": scored.actual_matched, "hits": scored.pair_hits,
        }
        rows.append(row)
        write_season(row)   # on disk before the next season starts
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

    null_precision = pooled_null_precision(season_nulls) * 100
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
