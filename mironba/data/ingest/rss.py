"""RSS ingest and assisted curation: fetch legitimately, draft, never accept.

    python -m mironba.data.ingest.rss --scenario lebron-2026

**Why RSS.** It carries a real publication timestamp, which is what the freeze
partition needs and what a screenshot would destroy. A feed whose items lack
``pubDate`` is excluded and reported, not patched with fetch time - fetch time
is when *we* looked, and the partition cares when the world knew.

**Scenario-scoped, not global.** A scenario declares a freeze date and a
subject set; ingest keeps articles published before the freeze that mention a
subject. PRE/POST is assigned from ``published_at``, never from which file a
row sits in.

**The LLM drafts; a human accepts.** Drafted rows land in a review queue, not
the store. ``append_confirmed_row`` is the ONLY writer into an evidence store
and it raises without an explicit confirmation - the discipline that caught
the phantom sixth suitor, a departure fact wearing an interest label, made
mechanical. A test asserts no other code path opens the store for writing.

**The limit, stated.** RSS carries recent items only. Historical news back to
2016 - what the ranker would need for its missing orthogonal features - is not
reachable this way. This generalises across current and future scenarios, not
backwards.
"""

from __future__ import annotations

import csv
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

EVIDENCE_ROOT = Path(__file__).resolve().parents[3] / "evidence"

#: League-level feeds. Team feeds follow the same shapes; add per scenario.
FEEDS = {
    "espn-nba": "https://www.espn.com/espn/rss/nba/news",
    "nba-com": "https://www.nba.com/rss/nba_rss.xml",
    "yahoo-nba": "https://sports.yahoo.com/nba/rss/",
}


class CurationError(RuntimeError):
    """A row tried to enter the store without confirmation."""


@dataclass(frozen=True)
class Article:
    feed: str
    url: str
    title: str
    summary: str
    published_at: str          # ISO-8601, from the feed, never from us
    fetched_at: str


def _fetch(url: str, timeout: int = 30) -> bytes:
    import urllib.request

    request = urllib.request.Request(
        url, headers={"User-Agent": "mironba-rss/1.0 (research; contact in repo)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_feed(feed_name: str, raw: bytes, fetched_at: str) -> tuple[list[Article], int]:
    """(articles with a real timestamp, count of items missing one)."""
    root = ET.fromstring(raw)
    articles, undated = [], 0
    for item in root.iter("item"):
        def text(tag):
            node = item.find(tag)
            return (node.text or "").strip() if node is not None else ""

        pub = text("pubDate")
        if not pub:
            undated += 1
            continue
        try:
            published = parsedate_to_datetime(pub).astimezone(timezone.utc)
        except (TypeError, ValueError):
            undated += 1
            continue
        articles.append(Article(
            feed=feed_name, url=text("link"), title=text("title"),
            summary=text("description"),
            published_at=published.isoformat(), fetched_at=fetched_at,
        ))
    return articles, undated


def scope_to_scenario(articles: list[Article], scenario) -> list[Article]:
    """Published before the scenario's freeze AND mentioning a subject.

    Subject matching is on the scenario's declared subject strings - ids and
    team codes - against title+summary. Declared list, not inference.
    """
    freeze = scenario.freeze.isoformat()
    kept = []
    for a in articles:
        if a.published_at[:10] > freeze:
            continue
        haystack = f"{a.title} {a.summary}"
        if any(s in haystack for s in scenario.subjects):
            kept.append(a)
    return kept


def draft_rows(article: Article, scenario, client=None) -> list[dict]:
    """Propose typed rows from one article. NEVER writes anywhere.

    Each draft quotes the source sentence it rests on, so the reviewer judges
    the claim against its text, not against the drafter's paraphrase. Without
    a client this returns an empty list - drafting is assistance, not a
    requirement, and hand curation remains the baseline path.
    """
    if client is None:
        return []
    from pydantic import BaseModel, Field

    class Draft(BaseModel):
        kind: str = Field(description="reported_interest or conditional_commitment")
        team: str = Field(description="team code, e.g. GSW")
        player_id: str = Field(description="basketball-reference id if inferable, else the name")
        condition: str = Field(default="", description="for conditionals only")
        commitment: str = Field(default="", description="for conditionals only")
        source_sentence: str = Field(description="the exact sentence, quoted verbatim")

    prompt = (
        f"Scenario subjects: {', '.join(scenario.subjects)}.\n"
        f"Article ({article.published_at}): {article.title}\n{article.summary}\n\n"
        "If this article reports a team's interest in a subject player, or a "
        "commitment conditional on an unresolved decision, describe it. Quote "
        "the exact sentence. If it reports neither, say so."
    )
    drafted = client.complete(
        [{"role": "user", "content": prompt}], schema=Draft, profile="report_agent",
        purpose="curation_draft",
    )
    return [{
        "kind": drafted.kind, "team": drafted.team, "player_id": drafted.player_id,
        "condition": drafted.condition, "commitment": drafted.commitment,
        "source_sentence": drafted.source_sentence,
        "url": article.url, "source": article.feed,
        "date": article.published_at[:10], "retrieved": article.fetched_at[:10],
    }]


def queue_path(scenario_id: str) -> Path:
    return EVIDENCE_ROOT / scenario_id / "review-queue.csv"


QUEUE_FIELDS = ("kind", "team", "player_id", "condition", "commitment",
                "source_sentence", "url", "source", "date", "retrieved")


def enqueue(scenario_id: str, rows: list[dict]) -> Path:
    """Drafts go to the queue. The queue is not the store."""
    path = queue_path(scenario_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.is_file()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=QUEUE_FIELDS)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in QUEUE_FIELDS})
    return path


def append_confirmed_row(scenario, row: dict, *, confirmed: bool = False) -> Path:
    """THE only writer into an evidence store, and it demands confirmation.

    ``confirmed=True`` is a human act, passed by the reviewer at the CLI -
    never set by drafting code. Phase is computed from the scenario's freeze
    and the row's own date; a row cannot declare its own side of the freeze.
    """
    if not confirmed:
        raise CurationError(
            "a drafted row may not enter the evidence store without explicit "
            "human confirmation. Review the queue and pass confirmed=True "
            "yourself - the phantom sixth suitor is what automatic acceptance "
            "produces."
        )
    kind = row["kind"]
    target = (
        scenario.evidence_dir / f"{scenario.id}-interest.csv"
        if kind == "reported_interest"
        else scenario.evidence_dir / f"{scenario.id}-conditionals.csv"
    )
    phase = "PRE" if row["date"] <= scenario.freeze.isoformat() else "POST"
    with target.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if kind == "reported_interest":
            writer.writerow([row["id"], row["team"], row["player_id"], row["date"],
                             row["source"], row["url"], row["retrieved"], phase,
                             row.get("anchors", ""), row.get("note", "")])
        else:
            writer.writerow([row["id"], row["team"], row["condition"],
                             row["commitment"], row["source"], row["date"],
                             row["url"], row["retrieved"], phase,
                             row.get("anchors", "")])
    return target


def main(argv=None) -> int:
    import argparse

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args(argv)

    from mironba.world.scenario import load_scenario

    scenario = load_scenario(args.scenario)
    fetched_at = datetime.now(timezone.utc).isoformat()
    print(f"{'feed':10} {'items':>6} {'dated':>6} {'undated':>8} {'oldest':>12} {'in-scope':>9}")
    for name, url in FEEDS.items():
        try:
            raw = _fetch(url)
        except Exception as exc:  # noqa: BLE001
            print(f"{name:10} EXCLUDED - fetch failed: {str(exc)[:60]}")
            continue
        articles, undated = parse_feed(name, raw, fetched_at)
        if undated and not articles:
            print(f"{name:10} EXCLUDED - no reliable timestamps")
            continue
        scoped = scope_to_scenario(articles, scenario)
        oldest = min((a.published_at[:10] for a in articles), default="-")
        print(f"{name:10} {len(articles)+undated:>6} {len(articles):>6} "
              f"{undated:>8} {oldest:>12} {len(scoped):>9}")
        if scoped:
            enqueue(scenario.id, [{
                "kind": "unclassified", "team": "", "player_id": "",
                "condition": "", "commitment": "",
                "source_sentence": a.title, "url": a.url, "source": a.feed,
                "date": a.published_at[:10], "retrieved": fetched_at[:10],
            } for a in scoped])
            print(f"{'':10} -> {len(scoped)} queued for review (NOT stored)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
