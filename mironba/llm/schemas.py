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


class TradeIntent(BaseModel):
    """Step two: what the GM wants, stated without a package.

    This is the entire vocabulary the model gets for a trade. It names players
    it wants, players it is willing to give up, players it refuses to give up,
    and an ordering over the willing set. It cannot pair an outgoing player
    with an incoming one — that pairing is a *package*, and packages are what
    the solver produces.

    The distinction is the whole of M1.5. Under the old design the model emitted
    packages and the validator rejected them, at a measured rate of 12 out of 12.
    Salary matching is integer constraint satisfaction; asking a language model
    to solve it and then scolding it for failing was the wrong shape of problem
    for the wrong tool.
    """

    target_player_ids: list[str] = Field(
        min_length=1,
        max_length=3,
        description="Ids of players you want to acquire, from the partner's roster.",
    )
    tradeable_asset_ids: list[str] = Field(
        min_length=1,
        max_length=12,
        description="Ids from your own roster you are willing to give up.",
    )
    excluded_player_ids: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Ids you refuse to trade under any circumstances.",
    )
    priority: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Your tradeable ids, most expendable first.",
    )
    reason: str = Field(min_length=1, max_length=REASON_MAX)


class PackageSelection(BaseModel):
    """Step three: pick one legal package, or decline them all.

    An index, not a package. The options were computed by the solver and are
    legal by construction, so the only thing left for judgement is which one —
    and whether any of them is worth doing at all, which is a genuine
    basketball question rather than an arithmetic one.
    """

    selection: int = Field(
        ge=-1,
        description="Index of the package you choose, or -1 to decline all of them.",
    )
    reason: str = Field(min_length=1, max_length=REASON_MAX)

    @property
    def declined(self) -> bool:
        return self.selection < 0


class StandPatReason(BaseModel):
    """Step two for the other branch. Exists so both branches are symmetric."""

    reason: str = Field(min_length=1, max_length=REASON_MAX)


#: Every schema an agent may be asked to fill. The test suite walks this to
#: assert the no-salaries rule, so a new schema is covered the moment it is
#: registered — and one that is not registered is caught too.
AGENT_SCHEMAS: tuple[type[BaseModel], ...] = (
    ActionChoice,
    TradeIntent,
    PackageSelection,
    StandPatReason,
)

#: Field names a model must never be allowed to fill. These are the values the
#: deterministic layer owns; a model that can type them can argue a trade into
#: legality without touching rules/.
FORBIDDEN_FIELD_TOKENS = (
    "salary",
    # Package vocabulary. A schema that can pair an outgoing player with an
    # incoming one can express an illegal trade; the solver owns that pairing.
    "send",
    "receive",
    "package",
    "offer",
    "outgoing",
    "incoming",
    "cap_hit",
    "payroll",
    "apron",
    "legal",
    "approved",
    "verdict",
    "valid",
)
