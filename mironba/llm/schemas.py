"""Agent-facing schemas. Small on purpose.

The charter's third defence is "keep schemas small; two-step any complex
action". These are the forms that implement it: an agent first picks an action
type, then fills in that action's parameters in a second call. There is no
schema here that nests a trade inside a decision, because that is the shape
small models fail on.

Two design rules, both enforced by tests:

**No salaries.** Nothing here lets a model state a dollar figure. A proposal
names players by id; the salaries come from the snapshot when the proposal is
assembled. A model that can type a number into a `salary` field can hallucinate
a trade into legality, and the validator would have no way to know.

**Literal, not Enum.** ``Literal["a", "b"]`` inlines the allowed values in the
JSON schema. A ``str, Enum`` subclass makes pydantic emit ``$ref``/``$defs``,
and grammar-constrained decoders vary in how well they follow references — a
schema the server cannot compile silently degrades to no constraint at all,
which is the one failure mode this module exists to prevent.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

#: Step one's vocabulary. Adding an action means adding a parameter schema for
#: it and a branch in the agent — not widening an existing form.
ActionType = Literal["propose_trade", "stand_pat"]

#: Generous, and deliberately so. An earlier 400-character cap turned a verbose
#: but perfectly well-formed rationale into a *schema failure*, which then
#: showed up in the headline number as if the model could not fill the form.
#: The cap exists to stop a runaway generation from being stored, not to
#: enforce brevity — the structure is what this schema is for. Prose length is
#: bounded by max_tokens, where it belongs.
REASON_MAX = 4000


class ActionChoice(BaseModel):
    """Step one: what kind of move, and why. Two fields, no nesting."""

    action: ActionType = Field(description="The kind of move to make this tick.")
    reason: str = Field(
        min_length=1,
        max_length=REASON_MAX,
        description="One or two sentences justifying the choice.",
    )


class TradeProposal(BaseModel):
    """Step two: the parameters of a trade, given the choice to propose one.

    Player ids only. Team ids are three-letter codes drawn from the roster the
    agent was shown, and every id is checked against that roster during
    assembly — a hallucinated player is a malformed proposal, not a trade with
    a mystery asset in it.
    """

    partner_team: str = Field(
        min_length=2,
        max_length=4,
        description="Three-letter code of the team to trade with.",
    )
    send_player_ids: list[str] = Field(
        min_length=1,
        max_length=4,
        description="Ids of players to send away, from your own roster.",
    )
    receive_player_ids: list[str] = Field(
        min_length=1,
        max_length=4,
        description="Ids of players to acquire, from the partner's roster.",
    )
    reason: str = Field(
        min_length=1,
        max_length=REASON_MAX,
        description="Why this trade serves your stated priorities.",
    )


class StandPatReason(BaseModel):
    """Step two for the other branch. Exists so both branches are symmetric."""

    reason: str = Field(min_length=1, max_length=REASON_MAX)


#: Every schema an agent may be asked to fill. The test suite walks this to
#: assert the no-salaries rule, so a new schema is covered the moment it is
#: registered — and one that is not registered is caught too.
AGENT_SCHEMAS: tuple[type[BaseModel], ...] = (
    ActionChoice,
    TradeProposal,
    StandPatReason,
)

#: Field names a model must never be allowed to fill. These are the values the
#: deterministic layer owns; a model that can type them can argue a trade into
#: legality without touching rules/.
FORBIDDEN_FIELD_TOKENS = (
    "salary",
    "cap_hit",
    "payroll",
    "apron",
    "legal",
    "approved",
    "verdict",
    "valid",
)
