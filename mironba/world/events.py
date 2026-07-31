"""The event log: one append-only record of everything that happened in a run.

One log, not one per channel. The charter's anti-goal list rules out dual
platform simulation, so an event carries a ``visibility`` field instead —
``internal`` for a GM's private reasoning, ``public`` for what the league would
see. M1 writes both; nothing reads ``visibility`` yet, and that is fine. Adding
the field later would mean backfilling it onto runs that never recorded it.

Every event carries the run id, because events are the artifacts most likely to
be read outside their directory — pasted into an issue, concatenated across
runs, loaded into a dataframe. An event that cannot name its run is an
observation with no provenance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mironba.world.manifest import ManifestError, Run

EVENT_LOG = "events.jsonl"


class EventType:
    """Stable event names. The eval harness will key off these strings."""

    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"

    AGENT_PROMPTED = "agent.prompted"
    AGENT_ACTION_CHOSEN = "agent.action_chosen"
    AGENT_PROPOSED = "agent.proposed"
    AGENT_INTENT = "agent.intent"
    AGENT_SELECTED = "agent.selected"
    SELECTION_OUT_OF_RANGE = "agent.selection_out_of_range"
    AGENT_STOOD_PAT = "agent.stood_pat"

    LLM_CALL = "llm.call"
    LLM_SCHEMA_FAILURE = "llm.schema_failure"
    LLM_REPAIR_ATTEMPT = "llm.repair_attempt"
    LLM_GAVE_UP = "llm.gave_up"

    PROPOSAL_ASSEMBLED = "proposal.assembled"
    PROPOSAL_MALFORMED = "proposal.malformed"
    SOLVER_RESULT = "solver.result"
    INTENT_UNSATISFIABLE = "solver.unsatisfiable"

    VERDICT = "rules.verdict"
    REJECTION_RETURNED = "agent.rejection_returned"


class Visibility:
    INTERNAL = "internal"
    PUBLIC = "public"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    ts: str
    type: str
    actor: str
    visibility: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
            "actor": self.actor,
            "visibility": self.visibility,
            "payload": self.payload,
        }


class EventLog:
    """Append-only. Writes through a ``Run``, so events inherit its run id."""

    def __init__(self, run: Run) -> None:
        if not isinstance(run, Run):
            raise ManifestError(
                f"an EventLog requires a Run, got {type(run).__name__}. "
                "Events without a run id cannot be attributed to a model or "
                "a code revision, which is what makes them evidence."
            )
        self.run = run
        self._events: list[Event] = []

    def emit(
        self,
        type: str,
        *,
        actor: str = "system",
        visibility: str = Visibility.INTERNAL,
        **payload: Any,
    ) -> Event:
        event = Event(
            seq=len(self._events),
            ts=datetime.now(UTC).isoformat(timespec="milliseconds"),
            type=type,
            actor=actor,
            visibility=visibility,
            payload=payload,
        )
        self._events.append(event)
        self.run.append_jsonl(EVENT_LOG, event.to_dict())
        return event

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)

    def of_type(self, type: str) -> list[Event]:
        return [e for e in self._events if e.type == type]

    def timeline(self) -> str:
        return "\n".join(
            f"  {e.seq:>3}  {e.type:<24} {e.actor}" for e in self._events
        )


def read_events(path: Path | str) -> list[dict]:
    """Read an events.jsonl back. Skips nothing and repairs nothing."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
