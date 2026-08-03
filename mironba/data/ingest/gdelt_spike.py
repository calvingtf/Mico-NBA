"""GDELT DOC 2.0 feasibility spike: one API, two known windows, a recall number.

    python -m mironba.data.ingest.gdelt_spike

Same shape as the Wayback spike: feasibility only, checked against rows that
already exist, stop at the verdict either way. Wayback answered 0/26 (the
source articles were never captured); this asks whether GDELT's news index
reaches what the curated stores rest on.

**The dating guarantee, stated before anything else.** GDELT's ``seendate``
is GDELT's OWN observation timestamp - when its crawler first saw the
article. That is the same evidentiary shape as a Wayback capture: a third
party attesting the text EXISTED by that moment. It is an UPPER BOUND on
publication, not a publication date - an article can be older than its
seendate, never newer. For the PRE/POST partition that is exactly the safe
direction: **seendate <= freeze admits an item as PRE conservatively** (it
may under-admit genuinely-PRE items GDELT saw late; it can never smuggle a
POST item into PRE). So seendate can be trusted as a PRE guarantee, and only
as an upper bound on age: the partition must gate on seendate and ignore the
page's self-reported date, and a backfilled row must record seendate as its
evidentiary timestamp with the self-reported date kept for display only.

**Recall is measured two ways**, because they answer different questions:

* (a) exact-source: is the SAME article the curator used in the index -
  the number comparable to the Wayback spike;
* (b) claim-level: does ANY indexed article in the window carry the same
  subject-team claim (matched on title text, an approximation stated as
  one) - the more useful number, since an equivalent ESPN report serves
  the same evidentiary purpose as a HoopsRumors post.

**Quota discipline.** GDELT rate-limits per IP and its 429 penalty windows
run long; the spike therefore batches subjects into OR-queries - seven
requests total - and retries a 429 exactly once after a long backoff. If it
is still throttled the verdict is "infeasible to measure today", claimed as
exactly that and nothing more.

No pipeline is built on a good result; a poor result ends it: historical
stays hand-curated, exactly as the Wayback spike concluded.
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[3] / "evidence"

DRAFT_WINDOW = ("20260501000000", "20260624000000")
LEBRON_WINDOW = ("20260510000000", "20260706000000")

PAUSE_S = 12
RETRY_BACKOFF_S = 240

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


def _query(raw_query: str, window: tuple[str, str], *, retried=False) -> list[dict]:
    params = urllib.parse.urlencode({
        "query": raw_query, "mode": "artlist", "format": "json",
        "startdatetime": window[0], "enddatetime": window[1],
        "maxrecords": "250", "sort": "datedesc",
    })
    request = urllib.request.Request(
        f"{DOC_API}?{params}",
        headers={"User-Agent": "mironba-gdelt-spike/1.0 (research)"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and not retried:
            time.sleep(RETRY_BACKOFF_S)
            return _query(raw_query, window, retried=True)
        raise
    except json.JSONDecodeError:
        return []
    time.sleep(PAUSE_S)
    return payload.get("articles", [])


def _or_batch(names: list[str]) -> str:
    return "(" + " OR ".join(f'"{n}"' for n in names) + ")"


def _norm_url(url: str) -> str:
    return url.lower().rstrip("/").replace("https://", "").replace(
        "http://", "").replace("www.", "")


def _draft_rows() -> list[dict]:
    with (EVIDENCE / "draft-2026" / "interest.csv").open(
            encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _lebron_rows() -> list[dict]:
    with (EVIDENCE / "lebron-2026" / "lebron-2026-interest.csv").open(
            encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


TEAM_WORDS = {
    "GSW": ("warriors", "golden state"), "OKC": ("thunder", "oklahoma"),
    "MIA": ("heat", "miami"), "MIL": ("bucks", "milwaukee"),
    "CHA": ("hornets", "charlotte"), "DAL": ("mavericks", "mavs", "dallas"),
    "LAC": ("clippers",), "ATL": ("hawks", "atlanta"),
    "CHI": ("bulls", "chicago"), "NOP": ("pelicans", "new orleans"),
    "WAS": ("wizards", "washington"), "CLE": ("cavaliers", "cavs"),
    "MIN": ("timberwolves", "wolves", "minnesota"),
    "PHI": ("76ers", "sixers", "philadelphia"),
}


def claim_matches(row: dict, articles: list[dict], subject_name: str) -> bool:
    """Approximate claim-level match: subject surname AND a team word appear
    in an indexed title inside the window. An approximation, stated as one -
    a title mentioning both can still be a different claim."""
    surname = subject_name.split()[-1].lower()
    words = TEAM_WORDS.get(row["team"], ())
    for article in articles:
        title = str(article.get("title", "")).lower()
        if surname in title and any(w in title for w in words):
            return True
    return False


def report_window(label: str, articles: list[dict]) -> None:
    if not articles:
        print(f"  {label}: 0 articles")
        return
    dates = sorted(str(a.get("seendate", ""))[:8] for a in articles)
    domains = {a.get("domain", "") for a in articles}
    seen_ok = sum(1 for a in articles if a.get("seendate"))
    print(f"  {label}: {len(articles)} articles, seendate on "
          f"{seen_ok}/{len(articles)}, {len(domains)} domains, "
          f"window {dates[0]}..{dates[-1]}")


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    print("GDELT DOC 2.0 SPIKE - feasibility only; stop at the verdict.")

    draft_rows = _draft_rows()
    lebron_rows = _lebron_rows()
    draft_subjects = sorted({r["player"] for r in draft_rows})
    batches = [draft_subjects[i:i + 6] for i in range(0, len(draft_subjects), 6)]

    articles_by_subject: dict[str, list[dict]] = {}
    all_articles: list[dict] = []
    plan = [("draft volume", '"NBA draft"', DRAFT_WINDOW, None)]
    plan += [(f"draft subjects {i + 1}/{len(batches)}", _or_batch(b),
              DRAFT_WINDOW, b) for i, b in enumerate(batches)]
    plan += [("lebron window", '"LeBron James"', LEBRON_WINDOW,
              ["LeBron James"]),
             ("davis window", '"Anthony Davis"', LEBRON_WINDOW,
              ["Anthony Davis"])]

    print(f"  request budget: {len(plan)} queries, {PAUSE_S}s apart")
    for label, raw, window, names in plan:
        try:
            articles = _query(raw, window)
        except Exception as exc:  # noqa: BLE001
            print(f"  query failed ({label}): {exc!r}")
            print("  RESULT: infeasible to measure today (throttled); "
                  "claimed as exactly that and nothing more.")
            return 1
        report_window(label, articles)
        all_articles.extend(articles)
        for name in names or []:
            bucket = articles_by_subject.setdefault(name, [])
            surname = name.split()[-1].lower()
            bucket.extend(a for a in articles
                          if surname in str(a.get("title", "")).lower())

    indexed_urls = {_norm_url(str(a.get("url", ""))) for a in all_articles}
    all_domains = {a.get("domain", "") for a in all_articles}

    def score(rows, subject_of):
        exact = claims = 0
        for row in rows:
            if _norm_url(row["url"]) in indexed_urls:
                exact += 1
            name = subject_of(row)
            if claim_matches(row, articles_by_subject.get(name, []), name):
                claims += 1
        return exact, claims

    draft_exact, draft_claims = score(draft_rows, lambda r: r["player"])
    NAMES = {"jamesle01": "LeBron James", "davisan02": "Anthony Davis"}
    lebron_exact, lebron_claims = score(
        lebron_rows, lambda r: NAMES.get(r["player_id"], r["player_id"]))

    print("\n  RECALL (a) exact source article indexed:")
    print(f"    draft rows  {draft_exact}/{len(draft_rows)}")
    print(f"    lebron rows {lebron_exact}/{len(lebron_rows)}")
    print("  RECALL (b) any article carrying the same claim (title-level "
          "approximation):")
    print(f"    draft rows  {draft_claims}/{len(draft_rows)}")
    print(f"    lebron rows {lebron_claims}/{len(lebron_rows)}")

    print(f"\n  DOMAINS represented ({len(all_domains)}):")
    for probe in ("hoopsrumors.com", "espn.com", "sports.yahoo.com",
                  "nbcsportsbayarea.com", "bleacherreport.com", "nba.com",
                  "cbssports.com", "si.com"):
        hit = any(probe in d for d in all_domains)
        print(f"    {'INDEXED ' if hit else 'absent  '} {probe}")

    total = len(draft_rows) + len(lebron_rows)
    claims_total = draft_claims + lebron_claims
    print(f"\n  VERDICT INPUTS: exact {draft_exact + lebron_exact}/{total}, "
          f"claim-level {claims_total}/{total}")
    if claims_total < total / 2:
        print("  RECALL IS POOR. Historical stays hand-curated, exactly as "
              "the Wayback spike concluded. Stop.")
    else:
        print("  RECALL SUPPORTS FEASIBILITY at claim level. Per the brief: "
              "no pipeline this round - the number stands; a full backfill "
              "is costed in the report, not built.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
