"""What every agent shares: a persona in numbers, a client, and a log.

Personas are structured parameters, never prose. The charter's anti-goal list
rules out prose personas for a specific reason: a paragraph of characterisation
cannot be fed to ``rules/``, cannot be swept in an experiment, and cannot be
compared across models in M5. A risk tolerance of 0.8 can do all three. It also
keeps the persona out of the part of the prompt where a model is most likely to
start improvising a character instead of filling a form.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from mironba.llm.client import LLMClient
from mironba.world.events import EventLog


class PersonaError(ValueError):
    """A persona parameter outside its declared range."""


@dataclass(frozen=True, slots=True)
class Persona:
    """Base persona. Every parameter is a number in a stated range.

    ``label`` is for reading logs, not for the prompt. If it ever reaches the
    model it becomes prose characterisation by the back door.
    """

    label: str = "generic"

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if name == "label":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PersonaError(
                    f"persona parameter {name!r} must be numeric, got "
                    f"{value!r}. Prose personas are an explicit anti-goal: a "
                    "parameter that cannot be swept or fed to rules/ is "
                    "characterisation, not configuration."
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_prompt_block(self) -> str:
        """Render as labelled numbers, not as a character sketch."""
        rows = [
            f"  {name}: {value}"
            for name, value in asdict(self).items()
            if name != "label"
        ]
        return "\n".join(rows)


class Agent:
    """An actor that decides once per tick.

    Subclasses implement ``decide``. The base class holds the wiring every
    agent needs and nothing else — M1 has one agent and one tick, so a
    scheduler-shaped base class here would be speculative.
    """

    role = "agent"

    def __init__(
        self,
        agent_id: str,
        persona: Persona,
        client: LLMClient,
        log: EventLog,
        profile: str | None = None,
    ) -> None:
        persona.validate()
        self.agent_id = agent_id
        self.persona = persona
        self.client = client
        self.log = log
        #: Role name, resolved to a model through configs/models.yaml. Agents
        #: name jobs, never models.
        self.profile = profile or f"{self.role}_agent"

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.agent_id} persona={self.persona.label}>"

    def decide(self, context: Any) -> Any:
        raise NotImplementedError
