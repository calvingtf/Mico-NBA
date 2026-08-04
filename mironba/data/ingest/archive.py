"""Standing RSS archive: poll twice daily, append forever, account for every day.

    python -m mironba.data.ingest.archive                    # poll + recover
    python -m mironba.data.ingest.archive --coverage         # gap report only
    python -m mironba.data.ingest.archive --catch-up         # poll right now
    python -m mironba.data.ingest.archive --window <scenario-id> [--lookback 90]

**Why this exists.** A feed carries roughly two days of items. A scenario
declared months after its freeze cannot fetch its own history - the live run
against a 27-day-old freeze found 0 in-scope items. The only fix is a clock:
poll on a schedule and keep everything. RSS therefore serves scenarios
**forward only**; historical scenarios remain hand-curated. ``--catch-up``
extends the archive forward by the feeds' actual reach (~2 days), not by a
window - coverage comes from the schedule, not from remembering to run it.

**Every day is accounted for, one of three ways:**

* items published that day, and/or a ``__poll__`` / ``__recovery__`` marker -
  the day is COVERED. A poll that returns nothing still writes its marker:
  an absent file and an empty result must be distinguishable, the same
  failure class as a sentinel standing in for absence.
* an ``__unrecoverable__`` marker - the day was missed and lay beyond every
  feed's measured reach when the miss was noticed. UNRECOVERABLE-BY-RSS,
  with the range recorded. Never retried, never reported as success.
* no file - a MISSING day ``coverage()`` will flag and the next poll will
  classify one way or the other.

**Recovery is honestly scoped.** On startup the poll measures each feed's
actual reach (today minus its oldest published item) instead of assuming two
days. A missing day within reach is genuinely recovered by the poll itself -
items published that day are still in the feed - and is marked with a
``__recovery__`` marker when the feed carried nothing for it. A missing day
beyond reach is marked UNRECOVERABLE-BY-RSS; a test asserts that path can
never produce a recovered status.
"""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from mironba.data.ingest.rss import FEEDS, Article, _fetch, parse_feed

ARCHIVE_ROOT = Path(__file__).resolve().parents[3] / "archive" / "rss"

FIELDS = ("feed", "url", "title", "summary", "published_at", "fetched_at")

#: Marker pseudo-feeds. Rows whose feed starts with "__" are bookkeeping, not
#: articles; every reader must filter them with is_marker().
POLL_MARKER = "__poll__"
RECOVERY_MARKER = "__recovery__"
UNRECOVERABLE_MARKER = "__unrecoverable__"

COVERED = "COVERED"
UNRECOVERABLE = "UNRECOVERABLE-BY-RSS"
MISSING = "MISSING"

#: Writer declaration for the enumerated writer test.
PARTITIONED = frozenset({"write_archive_rows"})
WHOLE_TABLE: frozenset = frozenset()


#: Absent-writer check (entry #62).
ACQUIRERS = {
    "poll": ("persists-per-unit",
             "write_archive_rows runs per feed inside the loop, and the "
             "__poll__ marker lands even on an empty poll"),
}


def is_marker(row: dict) -> bool:
    return row.get("feed", "").startswith("__")


def partition_path(published_at: str, root: Path = ARCHIVE_ROOT) -> Path:
    return root / f"{published_at[:10]}.csv"


def write_archive_rows(rows: list[dict], root: Path = ARCHIVE_ROOT) -> dict:
    """THE archive writer: append-only union by URL within each partition."""
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


def _marker_row(kind: str, day: date, note: str, stamp: str) -> dict:
    return {
        "feed": kind, "url": f"{kind.strip('_')}://{day.isoformat()}/{stamp}",
        "title": note, "summary": "", "published_at": day.isoformat(),
        "fetched_at": stamp,
    }


def rows_from(articles: list[Article]) -> list[dict]:
    return [{
        "feed": a.feed, "url": a.url, "title": a.title, "summary": a.summary,
        "published_at": a.published_at, "fetched_at": a.fetched_at,
    } for a in articles]


def read_partition(day: date, root: Path = ARCHIVE_ROOT) -> list[dict]:
    path = root / f"{day.isoformat()}.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def day_status(day: date, root: Path = ARCHIVE_ROOT) -> str:
    """COVERED / UNRECOVERABLE-BY-RSS / MISSING - the three-way distinction."""
    rows = read_partition(day, root)
    path = root / f"{day.isoformat()}.csv"
    if not path.is_file():
        return MISSING
    if any(not is_marker(r) for r in rows):
        return COVERED
    kinds = {r["feed"] for r in rows}
    if kinds & {POLL_MARKER, RECOVERY_MARKER}:
        return COVERED
    if UNRECOVERABLE_MARKER in kinds:
        return UNRECOVERABLE
    return COVERED  # a file exists with a header only: written, hence a poll


def _date_range(first: date, last: date):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def coverage(root: Path = ARCHIVE_ROOT, *, today: date | None = None) -> dict:
    """Every expected day from the first partition to today, classified.

    Returns statuses per day, the missing/unrecoverable lists, and the
    longest gap (consecutive not-COVERED days).
    """
    today = today or datetime.now(timezone.utc).date()
    parts = sorted(root.glob("*.csv"))
    if not parts:
        return {"first": None, "days": {}, "missing": [], "unrecoverable": [],
                "longest_gap": 0, "longest_gap_range": None}
    first = date.fromisoformat(parts[0].stem)
    days = {day: day_status(day, root) for day in _date_range(first, today)}
    missing = [d for d, s in days.items() if s == MISSING]
    unrecoverable = [d for d, s in days.items() if s == UNRECOVERABLE]
    longest, longest_range, run_start, run = 0, None, None, 0
    for day in _date_range(first, today):
        if days[day] != COVERED:
            run += 1
            run_start = run_start or day
            if run > longest:
                longest, longest_range = run, (run_start, day)
        else:
            run, run_start = 0, None
    return {"first": first, "days": days, "missing": missing,
            "unrecoverable": unrecoverable, "longest_gap": longest,
            "longest_gap_range": longest_range}


def recover(reach_days: dict[str, int], root: Path = ARCHIVE_ROOT, *,
            today: date | None = None, stamp: str = "") -> dict:
    """Classify every MISSING day: within measured reach, the poll that just
    ran has already appended anything the feeds carried, so the day becomes
    COVERED via a __recovery__ marker; beyond reach it is marked
    UNRECOVERABLE-BY-RSS. This function can never mark an out-of-reach day
    recovered - the marker kind is decided by the reach comparison alone.
    """
    today = today or datetime.now(timezone.utc).date()
    stamp = stamp or datetime.now(timezone.utc).isoformat()
    reach = max(reach_days.values(), default=0)
    report = coverage(root, today=today)
    recovered, unrecoverable = [], []
    for day in report["missing"]:
        age = (today - day).days
        if age <= reach:
            write_archive_rows([_marker_row(
                RECOVERY_MARKER, day,
                f"missed run recovered: day within measured feed reach "
                f"({age}d old <= {reach}d); feeds carried no items published "
                "this date beyond what is already stored", stamp)], root)
            recovered.append(day)
        else:
            write_archive_rows([_marker_row(
                UNRECOVERABLE_MARKER, day,
                f"UNRECOVERABLE-BY-RSS: {age}d old, beyond every feed's "
                f"measured reach ({reach}d). Not retried, not a success.",
                stamp)], root)
            unrecoverable.append(day)
    return {"reach_days": reach_days, "max_reach": reach,
            "recovered": recovered, "unrecoverable": unrecoverable}


def poll(root: Path = ARCHIVE_ROOT) -> int:
    """One poll of every feed, then honestly-scoped recovery of missed days."""
    stamp = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date()
    print(f"{'feed':10} {'dated':>6} {'undated':>8} {'window':>22} "
          f"{'appended':>9} {'dup':>5} {'reach':>6}")
    appended_total = 0
    reach_days: dict[str, int] = {}
    for name, url in FEEDS.items():
        try:
            raw = _fetch(url)
        except Exception as exc:  # noqa: BLE001 - a dead feed must not kill the poll
            print(f"{name:10} EXCLUDED - fetch failed: {str(exc)[:60]}")
            continue
        articles, undated = parse_feed(name, raw, stamp)
        if not articles:
            print(f"{name:10} EXCLUDED - no reliably dated items")
            continue
        dates = sorted(a.published_at[:10] for a in articles)
        reach_days[name] = (today - date.fromisoformat(dates[0])).days
        result = write_archive_rows(rows_from(articles), root)
        appended_total += result["appended"]
        print(f"{name:10} {len(articles):>6} {undated:>8} "
              f"{dates[0]}..{dates[-1]:>10} {result['appended']:>9} "
              f"{result['duplicate']:>5} {reach_days[name]:>5}d")

    # The poll marker: this run happened, even if it appended nothing. An
    # absent partition and an empty poll must never look the same.
    write_archive_rows([_marker_row(
        POLL_MARKER, today,
        f"poll ran: {len(reach_days)}/{len(FEEDS)} feeds reachable, "
        f"{appended_total} item(s) appended", stamp)], root)

    recovery = recover(reach_days, root, today=today, stamp=stamp)
    print(f"\nmeasured reach: " + (", ".join(
        f"{k}={v}d" for k, v in recovery["reach_days"].items()) or "none") +
        f"   (recovery scope: {recovery['max_reach']}d, measured not assumed)")
    if recovery["recovered"]:
        print(f"  recovered {len(recovery['recovered'])} missed day(s) within "
              f"reach: {', '.join(d.isoformat() for d in recovery['recovered'])}")
    if recovery["unrecoverable"]:
        print(f"  UNRECOVERABLE-BY-RSS {len(recovery['unrecoverable'])} day(s): "
              f"{recovery['unrecoverable'][0]} .. {recovery['unrecoverable'][-1]}")

    print_coverage(root)
    return 0


def print_coverage(root: Path = ARCHIVE_ROOT) -> None:
    report = coverage(root)
    if report["first"] is None:
        print("archive: empty")
        return
    days = report["days"]
    covered = sum(1 for s in days.values() if s == COVERED)
    print(f"\ncoverage: {covered}/{len(days)} day(s) COVERED since "
          f"{report['first']}")
    for day in report["missing"]:
        print(f"  MISSING       {day}")
    if report["unrecoverable"]:
        print(f"  UNRECOVERABLE {report['unrecoverable'][0]} .. "
              f"{report['unrecoverable'][-1]} "
              f"({len(report['unrecoverable'])} day(s), beyond feed reach)")
    if report["longest_gap"]:
        lo, hi = report["longest_gap_range"]
        print(f"  longest gap   {report['longest_gap']} day(s) ({lo} .. {hi})")


# --------------------------------------------------------------------------
# Scenario-window retrieval
# --------------------------------------------------------------------------


def window(scenario, lookback_days: int = 90, root: Path = ARCHIVE_ROOT) -> dict:
    """Read the archive across a declared lookback before a scenario's freeze.

    Coverage is REPORTED, never assumed: the report states days requested,
    days covered, every gap by range, and the item counts, before anything is
    returned. A window that predates the archive's first partition says so -
    the archive cannot testify about days before it existed.
    """
    end = scenario.freeze
    start = end - timedelta(days=lookback_days - 1)
    parts = sorted(root.glob("*.csv"))
    first = date.fromisoformat(parts[0].stem) if parts else None

    statuses: dict[date, str] = {}
    items, matching = [], []
    for day in _date_range(start, end):
        if first is None or day < first:
            statuses[day] = "BEFORE-ARCHIVE"
            continue
        statuses[day] = day_status(day, root)
        for row in read_partition(day, root):
            if is_marker(row):
                continue
            if row["published_at"][:10] > end.isoformat():
                continue
            items.append(row)
            haystack = f"{row['title']} {row['summary']}"
            if any(s in haystack for s in scenario.subjects):
                matching.append(row)

    gaps, run_start, prev = [], None, None
    for day in _date_range(start, end):
        bad = statuses[day] != COVERED
        if bad and run_start is None:
            run_start = day
        if not bad and run_start is not None:
            gaps.append((run_start, prev))
            run_start = None
        prev = day
    if run_start is not None:
        gaps.append((run_start, prev))

    covered = sum(1 for s in statuses.values() if s == COVERED)
    return {"start": start, "end": end, "requested": lookback_days,
            "covered": covered, "gaps": gaps, "statuses": statuses,
            "items": items, "matching": matching}


def render_window_report(scenario, report: dict) -> str:
    lines = [
        f"ARCHIVE WINDOW for {scenario.id}: {report['start']} .. {report['end']}",
        f"  days requested {report['requested']}, covered {report['covered']}"
        + ("" if report["covered"] == report["requested"] else
           f" - THE WINDOW HAS GAPS, listed below; drafts from this window "
           "describe the covered days only"),
    ]
    for lo, hi in report["gaps"]:
        kinds = {report["statuses"][d] for d in _date_range(lo, hi)}
        label = "/".join(sorted(kinds))
        span = f"{lo}" if lo == hi else f"{lo} .. {hi}"
        lines.append(f"  gap {span}  [{label}]")
    lines.append(f"  items in window {len(report['items'])}, "
                 f"matching subjects {len(report['matching'])}")
    return chr(10).join(lines)


def retrieve_for_scenario(scenario_id: str, lookback_days: int = 90,
                          root: Path = ARCHIVE_ROOT) -> int:
    from mironba.data.ingest.rss import enqueue
    from mironba.world.scenario import load_scenario

    print(announce(root))
    scenario = load_scenario(scenario_id)
    report = window(scenario, lookback_days, root)
    text = render_window_report(scenario, report)
    print(text)

    drafts = [{
        "kind": "unclassified", "team": "", "player_id": "",
        "condition": "", "commitment": "",
        "source_sentence": row["title"], "url": row["url"],
        "source": row["feed"], "date": row["published_at"][:10],
        "retrieved": row["fetched_at"][:10],
    } for row in report["matching"]]
    if drafts:
        enqueue(scenario.id, drafts)
    print(f"  drafts produced {len(drafts)}"
          + (" -> review queue (NOT the store)" if drafts else ""))

    # The scenario's own record carries the gap statement - not a failure,
    # but never quiet either.
    record = scenario.evidence_dir / "archive-window.txt"
    record.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with record.open("a", encoding="utf-8") as handle:
        handle.write(f"retrieved {stamp}{chr(10)}{text}{chr(10)}"
                     f"drafts produced {len(drafts)}{chr(10)}{chr(10)}")
    print(f"  window report appended to {record}")
    return 0


# --------------------------------------------------------------------------
# Health: visible on every read, loud when stale, one-line on demand
# --------------------------------------------------------------------------

#: Older than this many days, the newest partition means the schedule is
#: broken - the failure that costs the most and announces itself the least.
STALE_AFTER_DAYS = 2


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    out: list[tuple[date, date]] = []
    for day in sorted(days):
        if out and (day - out[-1][1]).days == 1:
            out[-1] = (out[-1][0], day)
        else:
            out.append((day, day))
    return out


def last_poll_stamp(root: Path = ARCHIVE_ROOT) -> str:
    """The newest __poll__ marker's own timestamp - when a poll last ran."""
    stamps = []
    for path in sorted(root.glob("*.csv"), reverse=True)[:14]:
        for row in read_partition(date.fromisoformat(path.stem), root):
            if row["feed"] == POLL_MARKER:
                stamps.append(row["fetched_at"])
    return max(stamps) if stamps else ""


def health(root: Path = ARCHIVE_ROOT, *, today: date | None = None,
           query_scheduler: bool = True) -> dict:
    today = today or datetime.now(timezone.utc).date()
    report = coverage(root, today=today)
    parts = sorted(root.glob("*.csv"))
    newest = date.fromisoformat(parts[-1].stem) if parts else None
    age = (today - newest).days if newest else None
    return {
        "first": report["first"],
        "newest": newest,
        "age_days": age,
        "stale": age is None or age > STALE_AFTER_DAYS,
        "last_poll": last_poll_stamp(root),
        "covered": sum(1 for s in report["days"].values() if s == COVERED),
        "expected": len(report["days"]),
        "missing": report["missing"],
        "unrecoverable_ranges": _ranges(report["unrecoverable"]),
        "longest_gap": report["longest_gap"],
        "longest_gap_range": report["longest_gap_range"],
        "next_run": _next_scheduled_run() if query_scheduler else "",
    }


def _next_scheduled_run() -> str:
    """Ask the OS scheduler when the next poll fires. Best effort."""
    import subprocess

    times = []
    for task in ("MiroNBA-RSS-Archiver", "MiroNBA-RSS-Archiver-PM"):
        try:
            out = subprocess.run(
                ["schtasks", "/Query", "/TN", task, "/FO", "LIST"],
                capture_output=True, text=True, timeout=15, check=True,
            ).stdout
            for line in out.splitlines():
                if line.strip().startswith("Next Run Time:"):
                    times.append(line.split(":", 1)[1].strip())
        except Exception:  # noqa: BLE001 - no scheduler is a reportable state
            continue
    return min(times) if times else "unknown (scheduler not queryable)"


def announce(root: Path = ARCHIVE_ROOT, *, today: date | None = None) -> str:
    """The banner every archive-reading entry point prints FIRST.

    Staleness is a loud state, not a quiet condition: if the newest
    partition is older than STALE_AFTER_DAYS the schedule is broken and the
    first line says so, with the last successful poll's timestamp. Coverage
    follows on every run - learning the archive has a hole must never
    require asking.
    """
    h = health(root, today=today, query_scheduler=False)
    lines = []
    if h["newest"] is None:
        lines.append("!! ARCHIVE EMPTY - no partition has ever been written; "
                     "the poller has never run here.")
    elif h["stale"]:
        lines.append(
            f"!! ARCHIVE STALE - newest partition {h['newest']} is "
            f"{h['age_days']} day(s) old (threshold {STALE_AFTER_DAYS}). The "
            f"schedule is broken; last successful poll "
            f"{h['last_poll'] or 'unknown'}.")
    gap = (f"; longest gap {h['longest_gap']}d "
           f"({h['longest_gap_range'][0]}..{h['longest_gap_range'][1]})"
           if h["longest_gap"] else "")
    unrec = sum((hi - lo).days + 1 for lo, hi in h["unrecoverable_ranges"])
    lines.append(
        f"archive health: {h['covered']}/{h['expected']} day(s) covered "
        f"since {h['first']}{gap}; {unrec} unrecoverable day(s)"
        if h["first"] else "archive health: empty")
    return chr(10).join(lines)


def print_health(root: Path = ARCHIVE_ROOT) -> None:
    """--health: the five-seconds-after-a-week-away view."""
    h = health(root)
    print(announce(root))
    print(f"  first partition   {h['first'] or '-'}")
    print(f"  last partition    {h['newest'] or '-'}"
          + (f"  ({h['age_days']}d old)" if h["newest"] else ""))
    print(f"  days covered      {h['covered']}/{h['expected']}")
    print(f"  last poll         {h['last_poll'] or 'never'}")
    if h["unrecoverable_ranges"]:
        for lo, hi in h["unrecoverable_ranges"]:
            span = f"{lo}" if lo == hi else f"{lo} .. {hi}"
            print(f"  UNRECOVERABLE     {span}")
    else:
        print("  UNRECOVERABLE     none")
    print(f"  next scheduled    {h['next_run']}")


def main(argv=None) -> int:
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ARCHIVE_ROOT)
    parser.add_argument("--coverage", action="store_true",
                        help="report gaps and exit; no fetch")
    parser.add_argument("--health", action="store_true",
                        help="one-line archive health: partitions, coverage, "
                             "unrecoverable ranges, next scheduled run")
    parser.add_argument("--catch-up", action="store_true",
                        help="poll right now. Extends the archive FORWARD by "
                             "the feeds' reach (~2 days), not by a window - "
                             "coverage comes from the schedule.")
    parser.add_argument("--window", metavar="SCENARIO_ID", default="",
                        help="read the archive across --lookback days before "
                             "the scenario's freeze; report coverage first")
    parser.add_argument("--lookback", type=int, default=90)
    args = parser.parse_args(argv)

    if args.health:
        print_health(args.root)
        return 0
    if args.window:
        return retrieve_for_scenario(args.window, args.lookback, args.root)
    print(announce(args.root))
    if args.coverage:
        print_coverage(args.root)
        return 0
    return poll(args.root)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
