"""Ask an agent why it did what it did.

    python -m mironba.agents.chat runs/<run-id> "why didn't you trade for Curry?"

The agent answers from its own record: the persona parameters it was given, the
events it produced, and — the part that matters — **the option set it was
actually shown**. A GM that declined a package declined a specific, enumerated
list, and that list is in the run. So the honest answer to "why did you decline"
is reconstructable rather than confabulated, and the prompt hands it over
explicitly so the model has nothing to invent.

## Money questions do not reach the model

The boundary from M1 holds here. An agent never saw a salary during the run, so
it cannot have an opinion about one afterwards, and a model asked "how much cap
room did you have" will produce a fluent number that is not evidence of
anything.

``looks_financial`` routes those questions to :func:`financial_answer`, which
reads the solver events in the run and reports what the *solver* recorded — or
says the run does not contain it. The model is not called at all. This is the
same reason the GM picks an index instead of writing a package: where a number
is available deterministically, asking a language model for it is strictly
worse.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from mironba.report import use_utf8_stdout
from mironba.report.timeline import Feed, load_run, name_of
from mironba.world.events import EventType

#: Questions that must be answered from the run, not by the model. Broad on
#: purpose: a false positive gets a precise answer from the event log, a false
#: negative gets a fluent invented figure.
FINANCIAL = re.compile(
    r"\b(salar(y|ies)|cap|apron|money|afford|\$|millions?|payroll|contracts?|"
    r"tax|exceptions?|matching|expensive|cheap|costs?|dollars?|price)\b",
    re.I,
)


def looks_financial(question: str) -> bool:
    return bool(FINANCIAL.search(question))


@dataclass
class Answer:
    text: str
    #: Where the answer came from. Always shown — a reader must be able to tell
    #: a solver record from a model's recollection.
    source: str
    options_shown: tuple[str, ...] = ()

    def render(self, width: int = 78) -> str:
        import textwrap

        lines = [f"  [{self.source}]"]
        lines += ["  " + line for line in textwrap.wrap(self.text, width - 4)]
        if self.options_shown:
            lines.append("")
            lines.append("  The option set this answer refers to:")
            for i, option in enumerate(self.options_shown):
                lines.append(f"    [{i}] {option}")
        return "\n".join(lines)


class AgentReply(BaseModel):
    """One short answer. No schema field invites a number, because the agent
    has no number to give."""

    answer: str = Field(
        description="Two to four sentences answering the question, using only "
        "the persona, events and option set provided. If the information is "
        "not in the context, say so plainly."
    )


def options_shown(events: list[dict]) -> tuple[str, ...]:
    """The packages the agent was actually offered, in the order it saw them.

    Reconstructed from ``proposal.assembled`` and the solver result, so "why
    did you decline option 2" has a referent. Where the run recorded a count
    but not the contents, the count is reported rather than a guess.
    """
    out: list[str] = []
    for event in events:
        if event["type"] != EventType.PROPOSAL_ASSEMBLED:
            continue
        raw = event.get("payload", {}).get("players", "")
        try:
            players = json.loads(str(raw).replace("'", '"'))
        except Exception:  # noqa: BLE001
            out.append(str(raw)[:120])
            continue
        # Names and direction only. The payload also carries each player's
        # salary; it is deliberately not read here, because this string is put
        # in front of the model and the agent never saw a salary during the run.
        rendered = []
        for player in players:
            if not isinstance(player, dict):
                rendered.append(str(player))
                continue
            label = name_of(player.get("id", "?"))
            source, dest = player.get("from"), player.get("to")
            if source and dest:
                label += f" ({source} -> {dest})"
            elif source:
                label += f" (from {source})"
            rendered.append(label)
        out.append(", ".join(rendered))
    if out:
        return tuple(out)
    for event in events:
        if event["type"] == EventType.SOLVER_RESULT:
            count = event.get("payload", {}).get("packages")
            if str(count).isdigit() and int(count) > 0:
                out.append(f"{count} legal package(s); contents not recorded in this run")
    return tuple(out)


def financial_answer(question: str, events: list[dict]) -> Answer:
    """Answer a money question from the solver's own record.

    Never calls the model. If the run does not contain the figure, that is the
    answer — an absent number is a fact about the run, and inventing one would
    be the exact failure the architecture is built to prevent.
    """
    facts: list[str] = []
    for event in events:
        payload = event.get("payload", {})
        kind = event["type"]
        if kind == EventType.TARGET_SCAN:
            ceiling = payload.get("ceiling")
            if ceiling:
                facts.append(
                    f"the solver's absorbable ceiling was ${int(ceiling):,}"
                )
            facts.append(
                f"{payload.get('considered', '?')} targets were considered and "
                f"{len(json.loads(str(payload.get('feasible', '[]')).replace(chr(39), chr(34))))}"
                " were reachable under salary matching"
            )
        elif kind == EventType.SOLVER_RESULT:
            binding = payload.get("binding_constraint")
            if binding and binding not in ("None", ""):
                facts.append(
                    f"on attempt {payload.get('attempt', '?')} the binding "
                    f"constraint was {binding}"
                )
        elif kind == EventType.INTENT_UNSATISFIABLE:
            facts.append(str(payload.get("constraint", "")).replace("\n", " ")[:220])

    if not facts:
        return Answer(
            "This run does not record a figure that answers that. The agent "
            "never saw a salary, and the solver events here do not contain the "
            "number, so there is nothing to report rather than something to "
            "estimate.",
            source="solver record - nothing found",
        )
    return Answer(
        "The agent never saw a salary, so this comes from the solver's record "
        "for this run: " + "; ".join(dict.fromkeys(facts)) + ".",
        source="solver record, not the model",
    )


SYSTEM = (
    "You are a team decision-maker being asked about choices you already made "
    "in a completed simulation. Answer only from the persona parameters, the "
    "event history and the option set given to you. You never saw salaries or "
    "contract terms and must not state any figure. If the answer is not in "
    "your context, say that plainly instead of guessing."
)


def build_prompt(question: str, feed: Feed, persona: dict, options: tuple[str, ...]) -> str:
    lines = [f"Your persona parameters: {persona or 'not recorded in this run'}", ""]
    lines.append("What you did, in order:")
    for entry in feed.entries:
        line = f"  - {entry.headline}"
        if entry.reasoning:
            line += f" (your stated reason: {entry.reasoning[:200]})"
        lines.append(line)
    lines.append("")
    if options:
        lines.append("The complete set of packages you were shown:")
        lines += [f"  [{i}] {option}" for i, option in enumerate(options)]
    else:
        lines.append("You were shown no legal packages in this run.")
    lines += ["", f"Question: {question}"]
    return "\n".join(lines)


class ChatAgent:
    def __init__(self, client, profile: str = "gm_agent") -> None:
        self.client = client
        self.profile = profile

    def ask(self, question: str, feed: Feed, events: list[dict], persona: dict | None = None) -> Answer:
        if looks_financial(question):
            return financial_answer(question, events)
        options = options_shown(events)
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": build_prompt(question, feed, persona or {}, options)},
        ]
        reply = self.client.complete(
            messages, schema=AgentReply, profile=self.profile, purpose="agent_chat"
        )
        return Answer(reply.answer, source="the agent, from its own record", options_shown=options)


def load_events(run_dir: Path | str) -> list[dict]:
    path = Path(run_dir) / "events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_persona(run_dir: Path | str) -> dict:
    manifest = Path(run_dir) / "manifest.json"
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("persona", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run")
    parser.add_argument("question", nargs="+")
    args = parser.parse_args(argv)
    use_utf8_stdout()
    question = " ".join(args.question)

    feed = load_run(args.run)
    events = load_events(args.run)

    if looks_financial(question):
        print(financial_answer(question, events).render())
        return 0

    from mironba.agents.report import report_client

    agent, _ = report_client(profile="gm_agent")
    chat = ChatAgent(agent.client, profile="gm_agent")
    print(chat.ask(question, feed, events, load_persona(args.run)).render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
