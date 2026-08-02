"""Standing RSS archive: poll daily, append forever, so the future has a past.

    python -m mironba.data.ingest.archive            # one poll, report, exit

**Why this exists.** A feed carries roughly two days of items. A scenario
declared months after its freeze cannot fetch its own history - the live run
against a 27-day-old freeze found 0 in-scope items, which is the limit
demonstrating itself. The only fix is a clock: poll every day and keep
everything, so a scenario declared later reads history that was captured as
it happened. RSS therefore serves scenarios **forward only**; historical
scenarios remain hand-curated, and the README says so.

**Shape.** Append-only, one writer, partitioned by published date:

    archive/rss/YYYY-MM-DD.csv      one file per published_at date

``published_at`` comes from the feed and ``fetched_at`` from us, recorded
separately - the partition cares when the world knew, not when we looked.
Re-polling never rewrites a row: the writer unions on URL within a partition,
which puts it under the same enumerated-writer discipline as every other
partitioned writer (three of those overwrote instead of merging before the
test existed; this one is declared and covered from birth).
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from mironba.data.ingest.rss import FEEDS, Article, _fetch, parse_feed

ARCHIVE_ROOT = Path(__file__).resolve().parents[3] / "archive" / "rss"

FIELDS = ("feed", "url", "title", "summary", "published_at", "fetched_at")

#: Writer declaration, mirrored from nba_stats: a writer must say whether it
#: takes partitioned data. The enumerated writer test discovers this module.
PARTITIONED = frozenset({"write_archive_rows"})
WHOLE_TABLE: frozenset = frozenset()


def partition_path(published_at: str, root: Path = ARCHIVE_ROOT) -> Path:
    return root / f"{published_at[:10]}.csv"


def write_archive_rows(rows: list[dict], root: Path = ARCHIVE_ROOT) -> dict:
    """THE archive writer: append-only union by URL within each partition.

    Returns {"appended": n, "duplicate": n}. Never deletes, never rewrites an
    existing row, never opens a partition with 'w' while holding only part of
    its data - the union is computed against what is already on disk.
    """
    root.mkdir(parents=True, exist_ok=True)
    by_partition: dict[Path, list[dict]] = {}
    for row in rows:
        by_partition.setdefault(partition_path(row["published_at"], root), []).append(row)

    appended = duplicate = 0
    for path, batch in sorted(by_partition.items()):
        seen: set[str] = set()
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                seen = {r["url"] for r in csv.DictReader(handle)}
        fresh = []
        for row in batch:
            if row["url"] in seen:
                duplicate += 1
                continue
            seen.add(row["url"])
            fresh.append(row)
        if not fresh:
            continue
        new_file = not path.is_file()
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            for row in fresh:
                writer.writerow({k: row.get(k, "") for k in FIELDS})
        appended += len(fresh)
    return {"appended": appended, "duplicate": duplicate}


def rows_from(articles: list[Article]) -> list[dict]:
    return [{
        "feed": a.feed, "url": a.url, "title": a.title, "summary": a.summary,
        "published_at": a.published_at, "fetched_at": a.fetched_at,
    } for a in articles]


def archive_span(root: Path = ARCHIVE_ROOT) -> tuple[str, str, int, int]:
    """(oldest partition, newest partition, partitions, rows) on disk."""
    parts = sorted(root.glob("*.csv"))
    if not parts:
        return ("-", "-", 0, 0)
    rows = 0
    for p in parts:
        with p.open(encoding="utf-8", newline="") as handle:
            rows += max(0, sum(1 for _ in handle) - 1)
    return (parts[0].stem, parts[-1].stem, len(parts), rows)


def poll(root: Path = ARCHIVE_ROOT) -> int:
    """One poll of every feed. Designed to be run by a scheduler, daily."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    print(f"{'feed':10} {'dated':>6} {'undated':>8} {'window':>22} "
          f"{'appended':>9} {'dup':>5}")
    per_feed_daily: dict[str, float] = {}
    for name, url in FEEDS.items():
        try:
            raw = _fetch(url)
        except Exception as exc:  # noqa: BLE001 - a dead feed must not kill the poll
            print(f"{name:10} EXCLUDED - fetch failed: {str(exc)[:60]}")
            continue
        articles, undated = parse_feed(name, raw, fetched_at)
        if not articles:
            print(f"{name:10} EXCLUDED - no reliably dated items")
            continue
        dates = sorted(a.published_at[:10] for a in articles)
        window = f"{dates[0]}..{dates[-1]}"
        span_days = max(
            1,
            (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days + 1,
        )
        per_feed_daily[name] = len(articles) / span_days
        result = write_archive_rows(rows_from(articles), root)
        print(f"{name:10} {len(articles):>6} {undated:>8} {window:>22} "
              f"{result['appended']:>9} {result['duplicate']:>5}")

    oldest, newest, parts, rows = archive_span(root)
    print(f"\narchive: {rows} row(s) across {parts} partition(s), "
          f"{oldest} .. {newest}")
    if per_feed_daily:
        daily = sum(per_feed_daily.values())
        print("projection, assuming feeds keep today's rates and the poller "
              "runs daily:")
        for horizon in (30, 90):
            print(f"  after {horizon:>2} days: ~{int(daily * horizon):>5} items "
                  f"covering a continuous {horizon}-day window "
                  f"(vs ~2 days reachable without the archive)")
        print("the projection is arithmetic on today's observed per-feed rates, "
              "not a measurement.")
    return 0


def main(argv=None) -> int:
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ARCHIVE_ROOT)
    args = parser.parse_args(argv)
    return poll(args.root)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
