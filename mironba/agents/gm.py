"""The GM agent: one decision, in two calls.

Three steps now, and the middle one is not an LLM call.

    1. choose an action        (LLM)
    2. state a TradeIntent     (LLM)   - what it wants, never a package
    3. pick from legal options (LLM)   - by index, or decline all

Between 2 and 3 the deterministic solver turns the intent into legal packages.
The model never emits a package and never sees a salary, so an illegal proposal
is not merely discouraged - it has no representation. M1 measured the previous
propose-then-validate design at 0 legal proposals in 12 attempts, with 9 repair
retries rescuing none. Salary matching is integer constraint satisfaction, and
a language model is the wrong instrument for it.

Steps stay separate for the reason they always did: a small model asked to emit
a decision *containing* a structure fails more often and unattributably - you
cannot tell whether it chose badly or merely typed badly.

What this agent may see: its own roster and cap situation, the counterparty
named by the scenario seed, and the seed itself. Not the other 28 teams.

That reading needs stating, because "its team's roster and cap situation, a
scenario seed, and nothing else" would leave a proposal with no counterparty to
name. The seed defines the counterparty — "Curry to the Lakers" names two teams
— so the partner's roster is part of the seed rather than league context. No
standings, no other teams' payrolls, no market.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mironba.agents.base import Agent, Persona
from mironba.llm.schemas import ActionChoice, PackageSelection, TradeIntent
from mironba.rules.cap import ApronTier
from mironba.rules.solver import FeasibleTarget
from mironba.world.events import EventType, Visibility

#: The two experiment arms, kept side by side rather than replaced.
#:
#: ``blind`` is the M1.5 behaviour: the model is shown a roster and a payroll
#: and asked what it wants. ``feasible`` additionally shows the solver-computed
#: list of players it could actually acquire.
#:
#: The old arm stays in the harness permanently. Without it "satisfiability is
#: N%" is a number with nothing to compare against, and the next change to the
#: prompt would have no baseline either. The delta is the result.
ARMS = ("blind", "feasible")


@dataclass(frozen=True, slots=True)
class GMPersona(Persona):
    """Structured, sweepable, and legible to code as well as to the model."""

    #: 0 = will not touch a deal that could go wrong; 1 = will.
    risk_tolerance: float = 0.5
    #: Seasons over which this GM expects to be judged. 1 = win now.
    win_now_horizon: int = 3
    #: 0 = will trade anyone; 1 = hoards young players and picks.
    asset_hoarding: float = 0.5

    def validate(self) -> None:
        # Explicit base call, not super(). `@dataclass(slots=True)` builds a
        # replacement class, and zero-arg super() closes over the original —
        # so super() here raises TypeError at runtime, not at definition.
        Persona.validate(self)
        for name in ("risk_tolerance", "asset_hoarding"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0, 1], got {value}")
        if not 1 <= self.win_now_horizon <= 10:
            raise ValueError(
                f"win_now_horizon must be in [1, 10], got {self.win_now_horizon}"
            )

    @property
    def max_assets_out(self) -> int:
        """How many players this GM will part with in one deal.

        The persona parameter feeding a deterministic constraint rather than
        only the prompt. It is stated to the model *and* checked at assembly,
        so a proposal that ignores it is caught rather than quietly honoured.
        """
        return 1 if self.asset_hoarding >= 0.75 else (2 if self.asset_hoarding >= 0.4 else 4)


@dataclass(frozen=True, slots=True)
class RosterEntry:
    player_id: str
    name: str
    salary: int

    def render(self) -> str:
        return f"  {self.player_id:<12} {self.name:<26} ${self.salary:,}"


@dataclass(frozen=True, slots=True)
class GMContext:
    """Everything the GM is allowed to know this tick."""

    team_id: str
    season: str
    scenario_seed: str
    own_roster: tuple[RosterEntry, ...]
    partner_team: str
    partner_roster: tuple[RosterEntry, ...]
    team_salary: int
    tier: ApronTier
    roster_count: int
    trade_date: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Who the solver says this team could actually acquire. Always computed,
    #: shown to the model only in the ``feasible`` arm — so the blind arm can
    #: report what the model *would* have been told, and a run where nobody was
    #: acquirable is distinguishable from one where the list was withheld.
    feasible_targets: tuple[FeasibleTarget, ...] = field(default_factory=tuple)

    def own_ids(self) -> set[str]:
        return {p.player_id for p in self.own_roster}

    def partner_ids(self) -> set[str]:
        return {p.player_id for p in self.partner_roster}


# --------------------------------------------------------------------------
# Prompt templates
#
# Module constants so manifest.template_hash() can hash the literal text. A
# reworded prompt is a different experiment, and M5 must not compare across one
# without noticing.
# --------------------------------------------------------------------------

SYSTEM_TEMPLATE = """\
You are the general manager of the {team_id} in the {season} NBA season.

Your decision-making parameters:
{persona_block}

Read them literally. risk_tolerance and asset_hoarding run 0 to 1.
win_now_horizon is the number of seasons over which you expect to be judged.

You do not decide whether a trade is legal. A separate rules engine checks the
collective bargaining agreement and will reject anything that does not comply.
Propose what you want; do not try to compute salary matching yourself.

Answer with JSON only, matching the schema you are given. No prose outside it.\
"""

CONTEXT_TEMPLATE = """\
Scenario: {scenario_seed}

Your team: {team_id}   payroll ${team_salary:,}   apron status: {tier}
Roster spots used: {roster_count} of 15
{notes_block}
Your roster:
{own_roster}

Trade partner available this tick: {partner_team}
Their roster:
{partner_roster}

You may send at most {max_assets_out} player(s) in one deal, given your
asset_hoarding of {asset_hoarding}.\
"""

ACTION_TEMPLATE = """\
{context}

Decide what to do this tick. Either propose a trade with {partner_team}, or
stand pat. Reply with JSON: {{"action": "propose_trade"|"stand_pat", "reason": "..."}}\
"""

INTENT_TEMPLATE = """\
{context}

You decided to pursue a trade. Your stated reason was: {reason}

State what you WANT. Do not build a trade and do not work out salary matching -
a rules engine will compute which combinations are legal and show you the legal
ones next.

  target_player_ids     who you want from {partner_team}
  tradeable_asset_ids   who you would be willing to give up
  excluded_player_ids   who you will not give up under any circumstances
  priority              your tradeable ids, most expendable first

Use the exact player ids shown above. Do not invent ids.\
"""

INTENT_FEASIBLE_TEMPLATE = """\
{context}

You decided to pursue a trade. Your stated reason was: {reason}

The rules engine has already worked out who you can legally acquire from
{partner_team}. These are the only players any legal trade can bring you, with
how many different legal packages exist for each:

{feasible}

Anyone not on that list cannot be acquired for any combination of your
contracts. Asking for one ends this tick with nothing.

State what you WANT. Do not build a trade and do not work out salary matching -
the engine will show you the legal packages for what you ask for.

  target_player_ids     who you want, chosen from the list above
  tradeable_asset_ids   who you would be willing to give up
  excluded_player_ids   who you will not give up under any circumstances
  priority              your tradeable ids, most expendable first

Use the exact player ids shown above. Do not invent ids. The list is computed
one player at a time, so asking for two of them together may still not fit.\
"""

SELECT_TEMPLATE = """\
{context}

The rules engine found these LEGAL packages. Every one complies with the CBA,
so you are choosing on basketball merit, not on legality.

{options}

Reply with the index of the package you want (0 to {last}), or -1 to decline
all of them if none is worth doing.\
"""

REVISE_INTENT_TEMPLATE = """\
{context}

Your stated intent has NO legal package.

  you wanted:       {targets}
  willing to trade: {tradeable}

{constraint}
{feasible_block}
State a revised intent. Changing what you want is the lever - a cheaper target,
more assets on the table, or one fewer exclusion.\
"""

#: Spliced into the revise prompt in the ``feasible`` arm only.
#:
#: An intent can be unsatisfiable in that arm even though every target came off
#: the list: feasibility is computed one player at a time and is not additive,
#: so two individually-acquirable players may be unaffordable together. Showing
#: the list again is the same intervention the arm is defined by, not a second
#: one — withholding it here would make the arm mean "told once, then not".
REVISE_FEASIBLE_BLOCK = """
Legally acquirable from {partner_team}, one at a time:

{feasible}

Asking for fewer of them at once is usually the fix.
"""

TEMPLATES = (
    SYSTEM_TEMPLATE,
    CONTEXT_TEMPLATE,
    ACTION_TEMPLATE,
    INTENT_TEMPLATE,
    INTENT_FEASIBLE_TEMPLATE,
    SELECT_TEMPLATE,
    REVISE_INTENT_TEMPLATE,
    REVISE_FEASIBLE_BLOCK,
)


def render_context(context: GMContext, persona: GMPersona) -> str:
    notes = "\n".join(f"Note: {n}" for n in context.notes)
    return CONTEXT_TEMPLATE.format(
        scenario_seed=context.scenario_seed,
        team_id=context.team_id,
        team_salary=context.team_salary,
        tier=context.tier.name.replace("_", " ").lower(),
        roster_count=context.roster_count,
        notes_block=(notes + "\n") if notes else "",
        own_roster="\n".join(p.render() for p in context.own_roster),
        partner_team=context.partner_team,
        partner_roster="\n".join(p.render() for p in context.partner_roster),
        max_assets_out=persona.max_assets_out,
        asset_hoarding=persona.asset_hoarding,
    )


@dataclass
class GMDecision:
    action: str
    reason: str
    intent: TradeIntent | None = None


class GMAgent(Agent):
    """Proposes. Never approves — that is ``rules/``, and only ``rules/``."""

    role = "gm"

    def __init__(
        self,
        agent_id,
        persona: GMPersona,
        client,
        log,
        profile=None,
        arm: str = "blind",
    ):
        super().__init__(agent_id, persona, client, log, profile)
        self.persona: GMPersona = persona
        if arm not in ARMS:
            raise ValueError(f"arm must be one of {ARMS}, got {arm!r}")
        #: Which prompt the intent step uses. Defaults to the old behaviour so
        #: an existing caller keeps measuring what it was measuring.
        self.arm = arm

    def _system(self) -> dict[str, str]:
        return {
            "role": "system",
            "content": SYSTEM_TEMPLATE.format(
                team_id=self.agent_id,
                season="{season}",
                persona_block=self.persona.as_prompt_block(),
            ),
        }

    def _messages(self, context: GMContext, user: str) -> list[dict[str, str]]:
        system = SYSTEM_TEMPLATE.format(
            team_id=context.team_id,
            season=context.season,
            persona_block=self.persona.as_prompt_block(),
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    # -- step one ---------------------------------------------------------

    def choose_action(self, context: GMContext) -> ActionChoice:
        rendered = render_context(context, self.persona)
        messages = self._messages(
            context,
            ACTION_TEMPLATE.format(
                context=rendered, partner_team=context.partner_team
            ),
        )
        self.log.emit(
            EventType.AGENT_PROMPTED,
            actor=self.agent_id,
            step="action_choice",
            prompt_chars=sum(len(m["content"]) for m in messages),
        )
        choice = self.client.complete(
            messages, schema=ActionChoice, profile=self.profile, purpose="action_choice"
        )
        self.log.emit(
            EventType.AGENT_ACTION_CHOSEN,
            actor=self.agent_id,
            visibility=Visibility.INTERNAL,
            action=choice.action,
            reason=choice.reason,
        )
        return choice

    # -- step two ---------------------------------------------------------

    def shows_feasible(self, context: GMContext) -> bool:
        """Whether this call actually gets the list.

        Both conditions matter. An empty list in the ``feasible`` arm falls
        back to the blind prompt rather than printing an empty heading, because
        "here is who you can get: (nothing)" invites the model to invent one,
        and because a run with nothing acquirable is a fact about the team that
        should not be dressed up as a withheld list.
        """
        return self.arm == "feasible" and bool(context.feasible_targets)

    def state_intent(self, context: GMContext, reason: str) -> TradeIntent:
        rendered = render_context(context, self.persona)
        if self.shows_feasible(context):
            user = INTENT_FEASIBLE_TEMPLATE.format(
                context=rendered,
                reason=reason,
                partner_team=context.partner_team,
                feasible="\n".join(t.render() for t in context.feasible_targets),
            )
        else:
            user = INTENT_TEMPLATE.format(
                context=rendered, reason=reason, partner_team=context.partner_team
            )
        messages = self._messages(context, user)
        self.log.emit(
            EventType.AGENT_PROMPTED,
            actor=self.agent_id,
            step="trade_intent",
            arm=self.arm,
            feasible_shown=self.shows_feasible(context),
            feasible_count=len(context.feasible_targets),
            prompt_chars=sum(len(m["content"]) for m in messages),
        )
        intent = self.client.complete(
            messages, schema=TradeIntent, profile=self.profile, purpose="trade_intent"
        )
        self.log.emit(
            EventType.AGENT_INTENT,
            actor=self.agent_id,
            visibility=Visibility.INTERNAL,
            targets=intent.target_player_ids,
            tradeable=intent.tradeable_asset_ids,
            excluded=intent.excluded_player_ids,
            reason=intent.reason,
        )
        return intent

    def revise_intent(
        self, context: GMContext, previous: TradeIntent, constraint: str
    ) -> TradeIntent:
        """One revised intent, given the binding constraint.

        Unlike the retry it replaces, this one has something to work with. The
        old loop handed back a rejection of a package the model had guessed at
        and asked it to guess again. This hands back the shape of the
        constraint - which rule bound, and by how many dollars - and asks for a
        different *want*, which is a question a language model can answer.
        """
        rendered = render_context(context, self.persona)
        block = ""
        if self.shows_feasible(context):
            block = REVISE_FEASIBLE_BLOCK.format(
                partner_team=context.partner_team,
                feasible="\n".join(t.render() for t in context.feasible_targets),
            )
        messages = self._messages(
            context,
            REVISE_INTENT_TEMPLATE.format(
                context=rendered,
                targets=", ".join(previous.target_player_ids),
                tradeable=", ".join(previous.tradeable_asset_ids),
                constraint=constraint,
                feasible_block=block,
            ),
        )
        self.log.emit(
            EventType.INTENT_UNSATISFIABLE,
            actor=self.agent_id,
            visibility=Visibility.INTERNAL,
            constraint=constraint,
        )
        intent = self.client.complete(
            messages,
            schema=TradeIntent,
            profile=self.profile,
            purpose="trade_intent_retry",
        )
        self.log.emit(
            EventType.AGENT_INTENT,
            actor=self.agent_id,
            visibility=Visibility.INTERNAL,
            attempt=2,
            targets=intent.target_player_ids,
            tradeable=intent.tradeable_asset_ids,
            reason=intent.reason,
        )
        return intent

    def select_package(
        self, context: GMContext, options: str, count: int
    ) -> PackageSelection:
        """Pick one legal package by index, or decline all of them."""
        rendered = render_context(context, self.persona)
        messages = self._messages(
            context,
            SELECT_TEMPLATE.format(context=rendered, options=options, last=count - 1),
        )
        self.log.emit(
            EventType.AGENT_PROMPTED,
            actor=self.agent_id,
            step="package_selection",
            options=count,
        )
        choice = self.client.complete(
            messages,
            schema=PackageSelection,
            profile=self.profile,
            purpose="package_selection",
        )
        # The schema bounds the index below but cannot know how many options
        # exist, so an out-of-range pick is read as a decline rather than
        # clamped onto a package the model did not choose.
        if choice.selection >= count:
            self.log.emit(
                EventType.SELECTION_OUT_OF_RANGE,
                actor=self.agent_id,
                selection=choice.selection,
                options=count,
            )
            choice = PackageSelection(selection=-1, reason=choice.reason)
        self.log.emit(
            EventType.AGENT_SELECTED,
            actor=self.agent_id,
            visibility=Visibility.PUBLIC,
            selection=choice.selection,
            declined=choice.declined,
            reason=choice.reason,
        )
        return choice

    # -- one tick ---------------------------------------------------------

    def decide(self, context: GMContext) -> GMDecision:
        choice = self.choose_action(context)
        if choice.action == "stand_pat":
            self.log.emit(
                EventType.AGENT_STOOD_PAT,
                actor=self.agent_id,
                visibility=Visibility.PUBLIC,
                reason=choice.reason,
            )
            return GMDecision(action="stand_pat", reason=choice.reason)
        intent = self.state_intent(context, choice.reason)
        return GMDecision(action="propose_trade", reason=choice.reason, intent=intent)
