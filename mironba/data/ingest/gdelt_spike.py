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

**Quota discipline, measured (entry #61).** Two egresses answered
differently: the home IP 429s on the FIRST request after 45 silent minutes
- saturated by another party; a tether egress answered four queries at 12s
spacing and then 429'd - our own budget, roughly four requests per rolling
window despite the documented one-per-5-seconds. Different causes, one
consequence each: no automatic retry (a request inside a penalty extends
it), and results PERSIST TO DISK AS EACH QUERY RETURNS - stamped with
egress and query label, append-only - so a later failure never loses
earlier successes (the incremental-backtest-writer lesson; the pre-fix run
discarded 991 fetched articles at its fifth query). ``--offline``
recomputes recall from persisted batches with zero network, stating
truncation beside the number: a capped query sorted newest-first covers
only the TAIL of its window, so absence in it is structural, not
evidentiary.

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

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

#: One JSON line per spike run: egress IP, verdict, and the recall numbers
#: when a run produces them - so a result is interpretable on its own later
#: (entry #60 is what an unlabelled 429 costs). Append-only.
RUN_RECORD = EVIDENCE / "spikes" / "gdelt-runs.jsonl"

#: Per-query article batches, one JSON line per query, appended the moment
#: the query returns - BEFORE the next request can fail the run.
ARTICLES = EVIDENCE / "spikes" / "gdelt-articles.jsonl"

#: Writer declaration for the enumerated writer test: append-only event logs,
#: a third declared kind - never opens 'w', never merges, only appends.
APPEND_ONLY = frozenset({"write_run_record", "write_articles"})
PARTITIONED: frozenset = frozenset()
WHOLE_TABLE: frozenset = frozenset()


def egress_ip() -> str:
    """The network this run speaks from, fetched BEFORE any GDELT request.

    A throttle verdict without the IP that produced it cannot be interpreted
    without asking the operator - the ambiguity entry #60 warns about.
    """
    try:
        request = urllib.request.Request(
            "https://api.ipify.org?format=json",
            headers={"User-Agent": "mironba-gdelt-spike/1.0 (research)"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())["ip"]
    except Exception as exc:  # noqa: BLE001 - unknown is reportable, not fatal
        return f"unknown (ipify unreachable: {type(exc).__name__})"


def write_run_record(record: dict, path: Path = RUN_RECORD) -> Path:
    """One JSON line appended per run, never rewritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + chr(10))
    return path


def write_articles(egress: str, label: str, window: tuple[str, str],
                   names: list, articles: list[dict],
                   path: Path = ARTICLES) -> Path:
    """Persist one query's results the moment they return. Append-only.

    A later failure must never lose earlier successes - the tethered run
    fetched 991 articles across four queries and the pre-fix code discarded
    all of them when the fifth 429'd.
    """
    from datetime import datetime, timezone

    line = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "egress_ip": egress, "label": label,
        "window_start": window[0], "window_end": window[1],
        "names": list(names), "returned": len(articles),
        "capped": len(articles) >= 250,
        "articles": [{"url": a.get("url", ""), "title": a.get("title", ""),
                      "seendate": a.get("seendate", ""),
                      "domain": a.get("domain", "")} for a in articles],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + chr(10))
    return path


def _query(raw_query: str, window: tuple[str, str]) -> list[dict]:
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
    except urllib.error.HTTPError:
        # No automatic retry: the tether measurement (entry #61) showed a
        # request inside a penalty window extends it, and persistence makes
        # stopping cheap - partial progress is already on disk.
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


# --------------------------------------------------------------------------
# Offline recall: persisted batches only, zero network
# --------------------------------------------------------------------------


def _effective_window(batch: dict) -> tuple[str, str]:
    """The slice of the window a batch actually searched, as yyyymmdd.

    A capped query sorted newest-first returns only the newest 250, so its
    coverage collapses to [oldest seendate returned .. window end]. An
    uncapped query covered its whole window.
    """
    end = batch["window_end"][:8]
    if not batch["capped"]:
        return batch["window_start"][:8], end
    seen = sorted(a["seendate"][:8] for a in batch["articles"] if a.get("seendate"))
    return (seen[0] if seen else end), end


def offline_recall(path: Path = ARTICLES) -> dict:
    """Draft-half recall from persisted batches. Never touches the network.

    The lebron half is reported UNMEASURED whenever its queries are absent
    from the persisted log - it is never inferred from the draft half.
    """
    if not path.is_file():
        return {"batches": []}
    batches = [json.loads(l) for l in
               path.read_text(encoding="utf-8").splitlines() if l.strip()]
    draft_batches = [b for b in batches if b["label"].startswith("draft")]
    lebron_batches = [b for b in batches
                      if b["label"] in ("lebron window", "davis window")]

    draft_rows = _draft_rows()
    indexed = {_norm_url(a["url"]) for b in batches for a in b["articles"]}
    draft_articles = [a for b in draft_batches for a in b["articles"]]

    exact = sum(1 for r in draft_rows if _norm_url(r["url"]) in indexed)
    claims = sum(1 for r in draft_rows
                 if claim_matches(r, draft_articles, r["player"]))

    never_searched = []
    for row in draft_rows:
        covered = False
        for batch in draft_batches:
            in_scope = (not batch["names"]) or (row["player"] in batch["names"])
            if not in_scope:
                continue
            lo, hi = _effective_window(batch)
            if lo <= row["date"].replace("-", "") <= hi:
                covered = True
                break
        if not covered:
            never_searched.append(row)

    return {
        "batches": batches, "draft_batches": draft_batches,
        "exact": exact, "claims": claims, "total": len(draft_rows),
        "never_searched": never_searched,
        "lebron_measured": bool(lebron_batches),
    }


def render_offline(path: Path = ARTICLES) -> int:
    result = offline_recall(path)
    print("OFFLINE RECALL - persisted batches only, zero network")
    if not result["batches"]:
        print("  no persisted batches on disk. The tethered run predates the")
        print("  persistence fix and its 991 articles were discarded at the")
        print("  fifth query (entry #61) - there is nothing to compute from.")
        print("  One 4-query tether session now yields the draft half, "
              "persisted as it goes.")
        return 0
    for batch in result["batches"]:
        lo, hi = _effective_window(batch)
        cap = "CAPPED at 250" if batch["capped"] else "uncapped"
        print(f"  {batch['label']:<22} {batch['returned']:>4} articles  {cap}"
              f"  effective window {lo}..{hi}  [egress {batch['egress_ip']}]")
    print(f"\n  draft recall (a) exact-source: {result['exact']}/{result['total']}")
    print(f"  draft recall (b) claim-level:  {result['claims']}/{result['total']}")
    ns = result["never_searched"]
    print(f"  TRUNCATION: {len(ns)} of {result['total']} curated rows fell in "
          "windows never fully searched -")
    print("  absence for these is structural, not evidentiary:")
    for row in ns:
        print(f"    {row['id']}  {row['date']}  {row['team']} / {row['player']}")
    if not result["lebron_measured"]:
        print("  lebron half: UNMEASURED - its queries are not in the "
              "persisted log, and it is not inferred from the draft half.")
    return 0


def main(argv=None) -> int:
    import argparse

    from datetime import datetime, timezone

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="recompute recall from persisted batches; "
                             "zero network calls")
    args = parser.parse_args(argv)
    if args.offline:
        return render_offline()

    print("GDELT DOC 2.0 SPIKE - feasibility only; stop at the verdict.")
    egress = egress_ip()
    record = {"ran_at": datetime.now(timezone.utc).isoformat(),
              "egress_ip": egress}

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

    print(f"  egress IP {egress}   request budget: {len(plan)} queries, "
          f"{PAUSE_S}s apart")
    persisted: dict = {}
    for label, raw, window, names in plan:
        try:
            articles = _query(raw, window)
        except Exception as exc:  # noqa: BLE001
            print(f"  query failed ({label}): {exc!r}")
            print(f"  RESULT: infeasible to measure today (throttled) from "
                  f"egress {egress}; claimed as exactly that and nothing more.")
            record.update(verdict="throttled", failed_at=label,
                          error=repr(exc), persisted=persisted)
            write_run_record(record)
            return 1
        report_window(label, articles)
        write_articles(egress, label, window, names or [], articles)
        persisted[label] = len(articles)
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
    print(f"\n  VERDICT INPUTS (egress {egress}): "
          f"exact {draft_exact + lebron_exact}/{total}, "
          f"claim-level {claims_total}/{total}")
    record.update(
        verdict="answered",
        persisted=persisted,
        recall_exact={"draft": draft_exact, "lebron": lebron_exact},
        recall_claim={"draft": draft_claims, "lebron": lebron_claims},
        domains=len(all_domains),
    )
    write_run_record(record)
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
