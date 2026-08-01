"""Dump every proposal as a row, so the ranker has negatives to learn from.

The pooled backtest recorded per-season *aggregates* — proposed, pairs, actual,
matched, hits. Enough to score the enumerator, useless for training a ranker,
which needs one row per proposal with its features and its label.

Written incrementally per season, merging, for the reason established three
rounds ago: a 2.5-hour run that only writes on completion loses everything to a
crash at season nine.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

OUT = Path("bench-proposals.csv")
FIELDS = ("season", "era", "team_a", "team_b", "is_real",
          "salary_similarity", "salary_magnitude", "roster_slot_distance",
          "record_gap", "disposition_pair", "value_moving", "value_gap",
          "complete")


def _merge_write(rows: list[dict], season: str, path: Path = OUT) -> None:
    stored: list[dict] = []
    if path.is_file():
        with path.open(encoding="utf-8", newline="") as handle:
            stored = [r for r in csv.DictReader(handle) if r["season"] != season]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in stored + rows:
            writer.writerow({k: row.get(k, "") for k in FIELDS})


def capture(season: str) -> int:
    from itertools import combinations

    from mironba.eval.ranker import extract
    from mironba.models.disposition import disposition
    from mironba.rules.constants import era_for_season
    from mironba.sim.deadline import (
        actual_deadline_trades, player_values, run,
    )
    from mironba.world.calendar import calendar_for

    result = run(season=season)
    values = player_values(season)
    dispositions = disposition(season, calendar_for(season).deadline)
    payroll, roster = {}, {}
    from mironba.eval.ranker import team_book

    payroll, roster, _ = team_book(season)

    real_pairs = {
        frozenset(c)
        for t in actual_deadline_trades(season)
        for c in combinations(sorted(t.teams), 2)
    }
    era = era_for_season(season)
    rows = []
    seen = set()

    def add(a, b, is_real, moving):
        key = (a, b)
        if key in seen:
            return
        seen.add(key)
        features = extract(season, a, b, payroll=payroll, roster=roster,
                           dispositions=dispositions, values=values,
                           moving_a=moving)
        # "complete" is the missingness restriction, applied to BOTH classes:
        # every player moving must carry a value. Negatives satisfy it by
        # construction; positives do not, which is the artifact channel.
        complete = all(p in values for p in moving) if moving else True
        rows.append({"season": season, "era": era, "team_a": a, "team_b": b,
                     "is_real": int(is_real), "complete": int(complete),
                     **features})

    for proposal in result.proposals:
        a, b = sorted(proposal.pair)
        add(a, b, frozenset((a, b)) in real_pairs,
            tuple(proposal.send) + tuple(proposal.receive))
    for trade in actual_deadline_trades(season):
        moving = tuple(m.player_id for m in trade.moves)
        for pair in combinations(sorted(trade.teams), 2):
            add(pair[0], pair[1], True, moving)

    _merge_write(rows, season)
    reals = sum(r["is_real"] for r in rows)
    print(f"{season} rows={len(rows)} positives={reals} "
          f"complete={sum(r['complete'] for r in rows)}", flush=True)
    return len(rows)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    from mironba.world.calendar import CALENDARS

    for season in sorted(CALENDARS):
        capture(season)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
