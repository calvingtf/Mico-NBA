"""The event log as a narrative feed.

    python -m mironba.report.timeline runs/<run-id>

The sim has emitted an ordered, attributed event log since M1, and nothing has
ever read it back as a story. This does, and it is deliberately a *rendering*
rather than a summary: no model is involved, nothing is inferred, and every
line traces to one event by sequence number. A reader who does not trust the
prose can check it against ``events.jsonl`` line for line.

**Refusals are the content.** The instinct is to render the accepted moves,
which on most runs is one trade at the end. But the measured behaviour of this
system is refusal — the model declined 16 of the 23 legal package sets it was
shown, and intents came back unsatisfiable more often than not. A feed that
renders only what succeeded would show the least representative 6% of the log
and would read as a working trade machine, which is the opposite of what was
measured.

So `solver.unsatisfiable`, `agent.stood_pat`, `rules.verdict` rejections and
declined selections are rendered with the same weight as accepted trades, and
the run summary counts them first.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from mironba.report import use_utf8_stdout
from mironba.world.events import EventType

#: player_id -> display name, for rendering only. Never used to decide
#: anything; a missing entry falls back to the id.
_NAMES: dict[str, str] = {}


def player_names() -> dict[str, str]:
    global _NAMES
    if _NAMES:
        return _NAMES
    import csv

    root = Path(__file__).resolve().parents[1] / "data" / "snapshots"
    for players in sorted(root.glob("bbref-*/players.csv")):
        with players.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                _NAMES.setdefault(row["player_id"], row["name"])
    return _NAMES


def name_of(player_id: str) -> str:
    return player_names().get(player_id, player_id)

#: Events that carry no narrative content of their own — they are bookkeeping
#: for another event that is rendered. Dropped from the feed, never from the
#: counts.
QUIET = {EventType.AGENT_PROMPTED, EventType.LLM_CALL}

#: How each event type reads in a feed. ``{}`` fields are payload keys.
#: Anything not listed falls through to a generic rendering rather than being
#: dropped: an unrendered event is a silent hole in the record.
PHRASING = {
    EventType.RUN_STARTED: "run begins - {scenario}, {team} with {partner}",
    EventType.TARGET_SCAN: "solver scans {considered} targets, {feasible_n} reachable",
    EventType.AGENT_ACTION_CHOSEN: "{actor} decides to {action}",
    EventType.AGENT_INTENT: "{actor} wants {targets_n} target(s): {targets}",
    EventType.SOLVER_RESULT: "solver: {packages} legal package(s), {binding}",
    EventType.INTENT_UNSATISFIABLE: "REFUSED by the rules - no legal package exists",
    EventType.AGENT_SELECTED: "{actor} {selection_verb}",
    EventType.AGENT_STOOD_PAT: "{actor} stands pat",
    EventType.PROPOSAL_ASSEMBLED: "package assembled: {n_players} player(s)",
    EventType.VERDICT: "verdict: {verdict}",
    EventType.SELECTION_OUT_OF_RANGE: "{actor} picked an index that does not exist",
    EventType.LLM_SCHEMA_FAILURE: "the model emitted output the schema rejected",
    EventType.LLM_REPAIR_ATTEMPT: "repair retry, feeding the validation error back",
    EventType.LLM_GAVE_UP: "gave up after the repair retry - failing loudly",
    EventType.PROPOSAL_MALFORMED: "proposal was malformed",
    EventType.REJECTION_RETURNED: "rejection handed back to the agent",
    EventType.RUN_FINISHED: "run ends - {verdict}",
}

#: Rendered with emphasis. These are the events a reader should not skim past,
#: and every one of them is a refusal or a failure.
NOTABLE = {
    EventType.INTENT_UNSATISFIABLE,
    EventType.AGENT_STOOD_PAT,
    EventType.SELECTION_OUT_OF_RANGE,
    EventType.LLM_SCHEMA_FAILURE,
    EventType.LLM_GAVE_UP,
    EventType.PROPOSAL_MALFORMED,
}


@dataclass(frozen=True, slots=True)
class Entry:
    seq: int
    ts: str
    actor: str
    headline: str
    reasoning: str = ""
    notable: bool = False
    kind: str = ""

    @property
    def clock(self) -> str:
        """Wall-clock time only. The date is the run's, shown in the header."""
        try:
            return datetime.fromisoformat(self.ts).strftime("%H:%M:%S")
        except ValueError:
            return self.ts[:8]


@dataclass
class Feed:
    run_id: str
    entries: list[Entry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def refusals(self) -> list[Entry]:
        return [e for e in self.entries if e.notable]


def _n(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, str) and value.startswith("["):
        try:
            return len(json.loads(value.replace("'", '"')))
        except Exception:  # noqa: BLE001
            return value.count(",") + 1 if value.strip("[]") else 0
    return int(value) if str(value).isdigit() else 0


def _headline(event: dict) -> str:
    kind, payload = event["type"], event.get("payload", {})
    template = PHRASING.get(kind)
    if template is None:
        return kind.replace(".", " ").replace("_", " ")

    fields = dict(payload)
    fields["actor"] = event.get("actor", "system")
    fields.setdefault("scenario", "?")
    fields.setdefault("team", "?")
    fields.setdefault("partner", "?")
    fields.setdefault("verdict", payload.get("verdict", "?"))
    fields.setdefault("action", payload.get("action", "?"))
    fields.setdefault("considered", payload.get("considered", "?"))
    fields.setdefault("packages", payload.get("packages", "?"))
    fields["feasible_n"] = _n(payload, "feasible")
    fields["targets_n"] = _n(payload, "targets")
    fields["n_players"] = _n(payload, "players")
    raw = str(payload.get("targets", "")).strip("[]").replace("'", "")
    fields["targets"] = ", ".join(
        name_of(t.strip()) for t in raw.split(",") if t.strip()
    ) or raw
    binding = payload.get("binding_constraint")
    fields["binding"] = (
        "unconstrained" if binding in (None, "None", "") else f"binding: {binding}"
    )
    declined = str(payload.get("declined", "")).lower() == "true"
    fields["selection_verb"] = (
        "DECLINES every package it was shown"
        if declined
        else f"picks option {payload.get('selection', '?')}"
    )
    try:
        return template.format(**fields)
    except KeyError:
        return kind.replace(".", " ").replace("_", " ")


def build_feed(events: list[dict], run_id: str = "") -> Feed:
    """Render an event list as a feed. Pure; no model, no inference."""
    feed = Feed(run_id=run_id)
    for event in events:
        kind = event["type"]
        if kind in QUIET:
            continue
        payload = event.get("payload", {})
        declined = str(payload.get("declined", "")).lower() == "true"
        notable = kind in NOTABLE or declined or (
            kind == EventType.VERDICT and payload.get("verdict") == "rejected"
        )
        feed.entries.append(
            Entry(
                seq=event["seq"],
                ts=event.get("ts", ""),
                actor=event.get("actor", "system"),
                headline=_headline(event),
                # The agent's own words, quoted rather than paraphrased. Where
                # an event has no reason field the line simply has none; this
                # never fabricates a motive for a decision.
                reasoning=str(payload.get("reason", "") or payload.get("constraint", "")),
                notable=notable,
                kind=kind,
            )
        )
        if kind == EventType.RUN_FINISHED:
            feed.summary = dict(payload)
    return feed


def load_run(run_dir: Path | str) -> Feed:
    path = Path(run_dir)
    events_file = path / "events.jsonl"
    if not events_file.is_file():
        raise FileNotFoundError(f"no events.jsonl in {path}")
    events = [
        json.loads(line)
        for line in events_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return build_feed(events, run_id=path.name)


def render(feed: Feed, width: int = 78) -> str:
    lines = ["=" * width, f"  TIMELINE  {feed.run_id}", "=" * width, ""]
    for entry in feed.entries:
        mark = "**" if entry.notable else "  "
        lines.append(f"{mark} {entry.clock}  {entry.seq:>3}  [{entry.actor}] {entry.headline}")
        if entry.reasoning:
            text = entry.reasoning.replace("\n", " ").strip()
            lines.append(f'          "{text[:width - 14]}"')
    refusals = feed.refusals
    lines += ["", "-" * width]
    lines.append(f"  {len(refusals)} refusal/failure event(s) of {len(feed.entries)} rendered")
    if feed.summary:
        summary = feed.summary
        lines.append(
            f"  verdict={summary.get('verdict', '?')}  "
            f"stood_pat={summary.get('stood_pat', '?')}  "
            f"declined_all={summary.get('declined_all', '?')}  "
            f"first_intent_satisfiable={summary.get('first_intent_satisfiable', '?')}"
        )
    lines.append("-" * width)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="path to a run directory containing events.jsonl")
    parser.add_argument("--backtest", default=None,
                        help="render the named backtest's dated PRE-freeze "
                        "interest above the feed, so a reader sees what was "
                        "known when. Inputs, not predictions.")
    args = parser.parse_args(argv)
    use_utf8_stdout()
    if args.backtest:
        from mironba.report.evidence_view import load_lebron_ledger, render_known_text

        ledger = load_lebron_ledger()
        if ledger:
            print(render_known_text(ledger))
    print(render(load_run(args.run)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
