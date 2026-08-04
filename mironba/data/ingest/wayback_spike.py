"""Wayback CDX feasibility spike: one publisher, one window, a recall number.

    python -m mironba.data.ingest.wayback_spike

**What this is.** RSS reaches back days; the archive reaches back to the day
it started; everything earlier is hand-curated, and days the poller missed
beyond feed reach are UNRECOVERABLE-BY-RSS. The Internet Archive's CDX index
is the one route that could serve both: historical backfill for past
scenarios, and the only possible fill for those unrecoverable gaps.

**Why a capture timestamp is the right kind of evidence.** The PRE/POST
partition needs "the world knew this by date D". A page's self-reported date
is the page's own claim about itself; a Wayback capture timestamp is a third
party attesting the text EXISTED on the capture date - strictly stronger,
and exactly the property PRE requires. (It bounds existence from above:
a capture proves the text is at least that old, never that it is newer.)

**What this is NOT.** A pipeline. It is a feasibility measurement against
the 2026 draft period, where 26 hand-curated interest rows from one
publisher (hoopsrumors.com) already exist to check recall against. If recall
is poor, that is the answer: historical stays hand-curated and gaps stay
declared. And on a good result the next step is a report, not a build.
"""

from __future__ import annotations

import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[3] / "evidence"

#: Absent-writer check (entry #62): this concluded spike HOLDS its CDX
#: results in memory - the printed report is the artifact, the conclusion
#: (0/26, never captured) is recorded in entry #58, and CDX answered
#: unthrottled, so a re-run is cheap. Declared rather than silently kept.
ACQUIRERS = {
    "cdx_query": ("holds-in-memory", "report-only spike; conclusion recorded"),
    "snapshot_fetchable": ("holds-in-memory", "boolean probe; nothing to keep"),
    "_ever_captured": ("holds-in-memory", "boolean probe; nothing to keep"),
}

CDX = "https://web.archive.org/cdx/search/cdx"
PUBLISHER = "hoopsrumors.com"
#: The 2026 draft period: first curated row (May 10) to draft night (June 23).
WINDOW = ("20260501", "20260624")


def cdx_query(url_pattern: str, from_ts: str, to_ts: str,
              timeout: int = 60) -> list[list[str]]:
    params = urllib.parse.urlencode({
        "url": url_pattern, "matchType": "prefix", "output": "json",
        "from": from_ts, "to": to_ts, "filter": "statuscode:200",
        "collapse": "urlkey", "limit": "3000",
        "fl": "urlkey,timestamp,original,statuscode",
    })
    request = urllib.request.Request(
        f"{CDX}?{params}",
        headers={"User-Agent": "mironba-wayback-spike/1.0 (research)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read() or b"[]")
    return payload[1:] if payload else []


def snapshot_fetchable(timestamp: str, original: str, timeout: int = 60) -> bool:
    """Does the capture resolve to retrievable text (first bytes suffice)?"""
    url = f"https://web.archive.org/web/{timestamp}id_/{original}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "mironba-wayback-spike/1.0 (research)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return bool(response.read(2048))
    except Exception:  # noqa: BLE001 - a dead snapshot is a data point
        return False


def _ever_captured(url: str, timeout: int = 60) -> bool:
    """Distinguish captured-late from never-captured: a late capture fails
    PRE (it attests existence only at ITS date) but an unbounded backfill
    could still discover the page; never-captured means no route exists."""
    params = urllib.parse.urlencode({"url": url, "output": "json", "limit": "1",
                                     "fl": "timestamp"})
    request = urllib.request.Request(
        f"{CDX}?{params}",
        headers={"User-Agent": "mironba-wayback-spike/1.0 (research)"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return len(json.loads(response.read() or b"[]")) > 1
    except Exception:  # noqa: BLE001
        return False


def curated_urls(draft_year: int = 2026) -> dict[str, int]:
    """URL -> number of curated interest rows resting on it."""
    path = EVIDENCE / f"draft-{draft_year}" / "interest.csv"
    counts: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            counts[row["url"]] = counts.get(row["url"], 0) + 1
    return counts


def _norm_url(url: str) -> str:
    return url.lower().rstrip("/").replace("https://", "").replace(
        "http://", "").replace("www.", "")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"WAYBACK CDX SPIKE - {PUBLISHER}, {WINDOW[0]}..{WINDOW[1]}")
    print("  feasibility only: on a poor recall this stops; on a good one it "
          "reports and waits.")

    captures: list[list[str]] = []
    for month in ("2026/05", "2026/06"):
        pattern = f"{PUBLISHER}/{month}"
        try:
            rows = cdx_query(pattern, *WINDOW)
        except Exception as exc:  # noqa: BLE001
            print(f"  CDX query failed for {pattern}: {exc}")
            print("  RESULT: infeasible to measure today; nothing is claimed. "
                  "Historical stays hand-curated.")
            return 1
        print(f"  {pattern}*: {len(rows)} distinct captured URL(s)")
        captures.extend(rows)

    if not captures:
        print("  RESULT: zero captures in the window. Recall is 0/26; "
              "historical stays hand-curated and gaps stay declared.")
        return 0

    sample = captures[:: max(1, len(captures) // 5)][:5]
    fetchable = sum(snapshot_fetchable(ts, orig) for _, ts, orig, _ in sample)
    print(f"  snapshot resolution: {fetchable}/{len(sample)} of a spread "
          "sample fetch to text")

    by_url = {_norm_url(orig): ts for _, ts, orig, _ in captures}
    counts = curated_urls()
    discoverable_rows = 0
    print(f"\n  recall against the {sum(counts.values())} curated rows "
          f"({len(counts)} source URLs):")
    for url, n in sorted(counts.items()):
        ts = by_url.get(_norm_url(url))
        if ts:
            discoverable_rows += n
            print(f"    FOUND  capture {ts[:8]}  {n} row(s)  {url[:70]}")
        else:
            status = "captured outside the window" if _ever_captured(url)                 else "NEVER CAPTURED at all"
            print(f"    {status:29} {n} row(s)  {url[:70]}")
    total = sum(counts.values())
    print(f"\n  RESULT: {discoverable_rows}/{total} curated rows rest on URLs "
          "this route captures inside the window"
          f" ({len(captures)} candidate URLs would still need drafting and "
          "human confirmation - discovery is not extraction).")
    if discoverable_rows < total // 2:
        print("  Recall is poor. Historical stays hand-curated; gaps stay "
              "declared rather than filled.")
    else:
        print("  Recall supports feasibility. Per the brief: REPORT AND WAIT - "
              "no pipeline is built on this result.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
