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

#: The offseason vocabulary. Signing is a different action from trading, with a
#: different solver behind it, so it gets its own enum value rather than being
#: squeezed into "propose_trade".
OffseasonActionType = Literal["sign_player", "propose_trade", "stand_pat"]

#: The signing routes an agent may name. Deliberately the route *labels* and
#: nothing else — naming a lever is not stating what the lever is worth, and
#: the solver still produces every term.
SigningRouteName = Literal[
    "cap_space", "bird", "early_bird", "non_bird", "non_taxpayer_mle",
    "taxpayer_mle", "room_exception", "bi_annual", "minimum",
]

#: Bounded by what the grammar compiler will accept, not by taste.
#:
#: This was 4000, chosen so a verbose rationale could not be counted as a schema
#: failure the way an earlier 400-character cap had been. On Ollama 0.32.5 that
#: value makes the server reject the request outright:
#:
#:     HTTP 400 "Failed to initialize samplers: failed to parse grammar"
#:
#: Bisected on this machine: ``maxLength`` up to 1999 compiles, 2000 and above
#: does not. The compiler appears to expand the bound into a bounded repetition
#: and give up past a fixed ceiling. 1500 sits clear of the cliff with room for
#: roughly 250 words, which is a rationale rather than an essay.
#:
#: The failure mode has inverted, and that is why a smaller cap is now safe.
#: Under an ignored schema the cap was a *validation* limit and overrunning it
#: failed the parse. Under an enforced grammar it is a *decoding* limit: the
#: model is made to close the string, so the reason is clipped rather than lost.
REASON_MAX = 1500


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


class OffseasonAction(BaseModel):
    """Step one of an offseason tick: sign, trade, or stand pat."""

    action: OffseasonActionType = Field(
        description="The kind of move to make this tick."
    )
    reason: str = Field(min_length=1, max_length=REASON_MAX)


class SigningIntent(BaseModel):
    """Who to sign and by which route. Never for how much.

    The signing analogue of ``TradeIntent``, and it holds the same line. A
    model that could state a first-year salary could sign a player its team
    cannot afford, and the cap sheet would have no way to know — the same
    failure the trade solver exists to prevent, one action type over.

    The route is named rather than chosen by the solver because which
    exception a team spends is a genuine strategic choice with consequences
    the solver cannot weigh: using the non-taxpayer mid-level hard-caps the
    team at the first apron for the rest of the year. That is a basketball
    decision. What it is worth is not.
    """

    target_player_ids: list[str] = Field(
        min_length=1,
        max_length=3,
        description="Ids of free agents to sign, from the list you were shown.",
    )
    route: SigningRouteName = Field(
        description="Which signing route to use for them."
    )
    reason: str = Field(min_length=1, max_length=REASON_MAX)


class StandPatReason(BaseModel):
    """Step two for the other branch. Exists so both branches are symmetric."""

    reason: str = Field(min_length=1, max_length=REASON_MAX)


#: Every schema an agent may be asked to fill. The test suite walks this to
#: assert the no-salaries rule, so a new schema is covered the moment it is
#: registered — and one that is not registered is caught too.
AGENT_SCHEMAS: tuple[type[BaseModel], ...] = (
    ActionChoice,
    OffseasonAction,
    TradeIntent,
    SigningIntent,
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
