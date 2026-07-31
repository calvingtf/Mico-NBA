"""The LLM -> rules boundary. The one place a proposal becomes a Trade.

The charter's first non-negotiable: *an LLM may propose a trade; only rules/
may approve one*. This module is where that is made true rather than merely
intended, and it does it by controlling what a proposal is allowed to contain.

A ``TradeProposal`` carries player ids and nothing else. Every number the
validator reasons about — salaries, payroll, apron tier — is looked up here
from the snapshot. So a model cannot argue its way past salary matching by
misstating a figure, because it never states one. The worst a bad proposal can
do is name the wrong players, and that is checked against the roster it was
shown.

Assembly can fail three ways, all of them things a real model does: an id that
is not on either roster, a player sent from the wrong side, or more assets out
than the persona permits. Those are ``MalformedProposal`` — distinct from a
rules rejection, and counted separately, because "the model typed a name that
does not exist" and "the model proposed an illegal trade" are different
findings about a model and averaging them tells you nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from mironba.agents.gm import GMContext, GMPersona
from mironba.llm.schemas import TradeProposal
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    TeamTradeState,
    Trade,
    TradeValidation,
    Verdict,
    validate_trade,
)


class MalformedProposal(ValueError):
    """The proposal could not be turned into a trade at all.

    Not a rejection. A rejection means the CBA said no to a coherent trade;
    this means there was no trade to judge.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass(frozen=True, slots=True)
class BYCResolution:
    """How base-year compensation was resolved for this scenario.

    The snapshot cannot answer it — Basketball-Reference does not publish
    re-sign status, so every player loads as UNKNOWN and every trade comes back
    UNDETERMINED. A scenario may assert an answer, but the assertion is a human
    input like any other and is recorded as one: ``sourced`` is False unless
    somebody actually checked, and the manifest carries the flag either way.
    """

    mode: str = "unresolved"  # unresolved | assume_not_re_signed
    sourced: bool = False
    note: str = ""

    @property
    def status(self) -> ReSignStatus:
        if self.mode == "assume_not_re_signed":
            return ReSignStatus.NOT_RE_SIGNED
        return ReSignStatus.UNKNOWN

    @property
    def is_assumption(self) -> bool:
        return self.mode != "unresolved" and not self.sourced


def assemble(
    proposal: TradeProposal,
    context: GMContext,
    persona: GMPersona,
    *,
    trade_date: date,
    partner_salary: int,
    partner_roster_count: int,
    byc: BYCResolution,
) -> Trade:
    """Turn ids into a ``Trade``, with every figure taken from the snapshot."""
    reasons: list[str] = []

    partner = proposal.partner_team.strip().upper()
    if partner != context.partner_team.upper():
        reasons.append(
            f"partner_team {partner!r} is not the counterparty offered "
            f"({context.partner_team})"
        )

    own = {p.player_id: p for p in context.own_roster}
    theirs = {p.player_id: p for p in context.partner_roster}

    send = [pid.strip() for pid in proposal.send_player_ids]
    receive = [pid.strip() for pid in proposal.receive_player_ids]

    for pid in send:
        if pid not in own:
            reasons.append(
                f"send id {pid!r} is not on {context.team_id}'s roster"
                + (f" (it is on {context.partner_team}'s)" if pid in theirs else "")
            )
    for pid in receive:
        if pid not in theirs:
            reasons.append(
                f"receive id {pid!r} is not on {context.partner_team}'s roster"
                + (f" (it is on {context.team_id}'s)" if pid in own else "")
            )
    if len(set(send)) != len(send):
        reasons.append("the same player is sent twice")
    if len(set(receive)) != len(receive):
        reasons.append("the same player is received twice")
    if len(send) > persona.max_assets_out:
        reasons.append(
            f"sends {len(send)} players; persona asset_hoarding="
            f"{persona.asset_hoarding} permits at most {persona.max_assets_out}"
        )

    if reasons:
        raise MalformedProposal(reasons)

    players = tuple(
        [
            PlayerAsset(
                player_id=pid,
                name=own[pid].name,
                salary=own[pid].salary,          # from the snapshot, never the model
                from_team=context.team_id,
                to_team=context.partner_team,
                re_sign_status=byc.status,
            )
            for pid in send
        ]
        + [
            PlayerAsset(
                player_id=pid,
                name=theirs[pid].name,
                salary=theirs[pid].salary,       # from the snapshot, never the model
                from_team=context.partner_team,
                to_team=context.team_id,
                re_sign_status=byc.status,
            )
            for pid in receive
        ]
    )

    teams = (
        TeamTradeState(
            team_id=context.team_id,
            team_salary=context.team_salary,
            roster_count=context.roster_count,
        ),
        TeamTradeState(
            team_id=context.partner_team,
            team_salary=partner_salary,
            roster_count=partner_roster_count,
        ),
    )

    return Trade(
        season=context.season,
        trade_date=trade_date,
        teams=teams,
        players=players,
        label=proposal.reason[:120],
    )


def judge(trade: Trade) -> TradeValidation:
    """The only approval path in the codebase.

    A one-line wrapper on purpose: it gives the boundary a name that shows up
    in call graphs and in ``test_only_rules_may_approve``, so an agent that
    ever starts deciding legality for itself is visible rather than subtle.
    """
    return validate_trade(trade)


def rejection_reason(validation: TradeValidation) -> str:
    """What to hand back to the agent for its one retry.

    Errors only, and the rule ids with them. The agent gets the CBA's actual
    objection rather than a paraphrase, which is the difference between a
    revision and another guess.
    """
    if validation.verdict is Verdict.UNDETERMINED:
        return "\n".join(
            f"- UNDETERMINED {f.rule}: {f.message}" for f in validation.undetermined()
        )
    return "\n".join(f"- {f.rule}: {f.message}" for f in validation.errors())
