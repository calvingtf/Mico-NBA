"""Trade legality under the 2023 CBA.

``validate_trade`` is the only gate between a proposed trade and the world
state. Agents propose; this approves. It returns findings rather than raising,
because a rejected proposal is normal simulator traffic and the *reason* for
rejection is signal an agent can act on.

Severity contract:
  ERROR    the league would reject this trade. ``legal`` is False.
  WARNING  legal, but with a consequence a GM would care about (hard cap,
           dropping below the minimum team salary, roster shortfall).
  INFO     bookkeeping worth recording in the event log.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from mironba.rules.cap import (
    cannot_be_a_minimum_contract,
    ApronTier,
    MatchOutcome,
    TradeException,
    can_fit_without_aggregating,
    exception_match_limit,
    max_incoming_salary,
    pct_of,
    qualifies_for_minimum_exception,
    tier_for_salary,
)
from mironba.rules.constants import (
    AGGREGATION_WINDOW_DAYS,
    MAX_STANDARD_ROSTER,
    MIN_CASH_IN_TRADE,
    MIN_STANDARD_ROSTER,
    SIGNING_RESTRICTION_DAYS,
    SIGNING_RESTRICTION_MONTH_DAY,
    STEPIEN_HORIZON_YEARS,
    TRADE_CUSHION,
    CapEnvironment,
    environment_for,
)


class Severity(Enum):
    ERROR = "error"
    #: We cannot decide this trade. Not a soft error — a refusal to guess.
    UNDETERMINED = "undetermined"
    WARNING = "warning"
    INFO = "info"


class Verdict(Enum):
    """The three answers this validator can give.

    ``UNDETERMINED`` exists because some trades cannot be decided from the data
    we hold, and the honest answer is to say so. Collapsing it into REJECTED
    would look safe and be wrong: M4 backtests real trades, real trades include
    base-year-compensation cases, and silently scoring those as rejected would
    corrupt the hit rate in a way nobody would notice.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    UNDETERMINED = "undetermined"


class VerdictUndetermined(RuntimeError):
    """Raised when a caller asks for a boolean and there isn't one.

    ``TradeValidation.legal`` raises this rather than returning False, so an
    undecidable trade cannot be silently absorbed by ``if result.legal:``.
    """

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        joined = "; ".join(reasons) or "no reason recorded"
        super().__init__(
            f"trade legality is undetermined ({joined}). "
            "Inspect .verdict and .undetermined() instead of .legal."
        )


# Stable rule identifiers. Agents and the eval harness key off these strings,
# so they are part of the public contract — rename with care.
class Rule:
    STRUCTURE = "STRUCTURE"
    SALARY_MATCH = "SALARY_MATCH"
    AGGREGATION_SECOND_APRON = "AGGREGATION_SECOND_APRON"
    AGGREGATION_WINDOW = "AGGREGATION_WINDOW"
    TRADE_RESTRICTION_WINDOW = "TRADE_RESTRICTION_WINDOW"
    NO_TRADE_CLAUSE = "NO_TRADE_CLAUSE"
    ROSTER_LIMIT = "ROSTER_LIMIT"
    ROSTER_MINIMUM = "ROSTER_MINIMUM"
    CASH_LIMIT = "CASH_LIMIT"
    CASH_SECOND_APRON = "CASH_SECOND_APRON"
    TPE_PRIOR_YEAR = "TPE_PRIOR_YEAR"
    SIGN_AND_TRADE_APRON = "SIGN_AND_TRADE_APRON"
    STEPIEN = "STEPIEN"
    MIN_TEAM_SALARY = "MIN_TEAM_SALARY"
    HARD_CAP = "HARD_CAP"
    CASH_MINIMUM = "CASH_MINIMUM"
    BASE_YEAR_COMPENSATION = "BASE_YEAR_COMPENSATION"
    MINIMUM_SALARY_EXCEPTION = "MINIMUM_SALARY_EXCEPTION"


@dataclass(frozen=True, slots=True)
class Finding:
    rule: str
    severity: Severity
    message: str
    team: str | None = None
    detail: dict = field(default_factory=dict)

    def __str__(self) -> str:
        where = f"[{self.team}] " if self.team else ""
        return f"{self.severity.value.upper()} {self.rule}: {where}{self.message}"


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------


class ReSignStatus(Enum):
    """Whether a player re-signed with the team now trading him.

    A precondition for base-year compensation, and the field most likely to be
    absent from a data source — hence ``UNKNOWN`` as a first-class value rather
    than a boolean that quietly defaults to "no".
    """

    NOT_RE_SIGNED = "not_re_signed"
    RE_SIGNED_BIRD = "re_signed_bird"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PlayerAsset:
    """A player changing hands.

    ``salary`` is the current-season cap hit. ``outgoing_match_value`` exists
    for base-year compensation: a BYC player's salary counts differently for
    the team sending him than for the team receiving him, so the two sides of
    the same transaction legitimately use different numbers. Supplying it is
    also how a caller asserts it has already resolved BYC — see
    ``_check_base_year_compensation``.
    """

    player_id: str
    name: str
    salary: int
    from_team: str
    to_team: str
    outgoing_match_value: int | None = None
    acquired_via_trade_on: date | None = None
    signed_on: date | None = None
    trade_restricted_until: date | None = None
    sign_and_trade: bool = False
    no_trade_clause: bool = False
    consent_given: bool = False
    re_sign_status: ReSignStatus = ReSignStatus.NOT_RE_SIGNED
    #: Salary under the contract immediately before re-signing. Lets a caller
    #: rule BYC out by showing the raise was 20% or less.
    previous_salary: int | None = None
    #: Minimum salary exception inputs. Unknown experience falls back to the
    #: zero-year minimum, so the exception is granted only when certain.
    years_of_service: int | None = None
    contract_years: int | None = None
    salary_exceeded_minimum_previously: bool = False

    @property
    def match_value_out(self) -> int:
        """Salary the *sending* team gets credit for."""
        return self.salary if self.outgoing_match_value is None else self.outgoing_match_value


@dataclass(frozen=True, slots=True)
class PickAsset:
    from_team: str
    to_team: str
    draft_year: int
    round: int = 1
    protection: str | None = None


@dataclass(frozen=True, slots=True)
class CashAsset:
    from_team: str
    to_team: str
    amount: int


@dataclass(frozen=True, slots=True)
class TeamTradeState:
    """Everything the validator needs to know about one participating team."""

    team_id: str
    team_salary: int
    roster_count: int
    trade_exceptions: tuple[TradeException, ...] = ()
    cash_sent_this_year: int = 0
    cash_received_this_year: int = 0
    #: Draft year -> number of first-round picks the team controls, *before*
    #: the trade. Only years within the Stepien horizon matter.
    first_round_picks: tuple[tuple[int, int], ...] = ()

    def picks_by_year(self) -> dict[int, int]:
        return dict(self.first_round_picks)


@dataclass(frozen=True, slots=True)
class Trade:
    season: str
    trade_date: date
    teams: tuple[TeamTradeState, ...]
    players: tuple[PlayerAsset, ...] = ()
    picks: tuple[PickAsset, ...] = ()
    cash: tuple[CashAsset, ...] = ()
    #: Descriptive only; carried through to the event log.
    label: str = ""

    def team(self, team_id: str) -> TeamTradeState:
        for t in self.teams:
            if t.team_id == team_id:
                return t
        raise KeyError(f"team {team_id!r} is not a participant in this trade")

    @property
    def team_ids(self) -> list[str]:
        return [t.team_id for t in self.teams]


@dataclass
class TeamOutcome:
    team_id: str
    match: MatchOutcome
    tier_before: ApronTier
    tier_after: ApronTier
    roster_after: int
    outgoing_players: list[PlayerAsset] = field(default_factory=list)
    incoming_players: list[PlayerAsset] = field(default_factory=list)


@dataclass
class TradeValidation:
    verdict: Verdict
    findings: list[Finding]
    per_team: dict[str, TeamOutcome]
    season: str

    @property
    def legal(self) -> bool:
        """Whether the league would approve this trade.

        Raises ``VerdictUndetermined`` when there is no answer, rather than
        returning False. A caller that has not thought about undecidable trades
        gets an exception; one that has, reads ``verdict`` directly.
        """
        if self.verdict is Verdict.UNDETERMINED:
            raise VerdictUndetermined([f.message for f in self.undetermined()])
        return self.verdict is Verdict.APPROVED

    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ERROR]

    def undetermined(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.UNDETERMINED]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def explain(self) -> str:
        if not self.findings:
            return "approved; no findings"
        return "\n".join(str(f) for f in self.findings)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_trade(trade: Trade, env: CapEnvironment | None = None) -> TradeValidation:
    """Decide whether the league would approve ``trade``."""
    env = env or environment_for(trade.season)
    findings: list[Finding] = []

    structural = _check_structure(trade, findings)
    if structural:
        # Downstream checks index into teams by id; bail rather than crash on
        # a malformed proposal, which is a realistic thing for an agent to emit.
        return TradeValidation(Verdict.REJECTED, findings, {}, trade.season)

    per_team: dict[str, TeamOutcome] = {}
    for state in trade.teams:
        outgoing = [p for p in trade.players if p.from_team == state.team_id]
        incoming = [p for p in trade.players if p.to_team == state.team_id]
        per_team[state.team_id] = _check_team(state, outgoing, incoming, trade, env, findings)

    _check_cash(trade, per_team, env, findings)
    _check_picks(trade, findings)

    return TradeValidation(_verdict(findings), findings, per_team, trade.season)


def _verdict(findings: list[Finding]) -> Verdict:
    """Fold findings into one answer.

    A definite ERROR outranks an UNDETERMINED. That ordering is deliberate and
    specific to what BYC can do: it only ever *lowers* a sending team's
    outgoing match value, so it can turn a legal trade illegal but never the
    reverse. A trade already rejected on the numbers stays rejected however the
    BYC question resolves.
    """
    if any(f.severity is Severity.ERROR for f in findings):
        return Verdict.REJECTED
    if any(f.severity is Severity.UNDETERMINED for f in findings):
        return Verdict.UNDETERMINED
    return Verdict.APPROVED


def _check_structure(trade: Trade, findings: list[Finding]) -> bool:
    """Returns True if the trade is malformed enough to stop validation."""
    fatal = False
    ids = trade.team_ids
    if len(ids) < 2:
        findings.append(
            Finding(Rule.STRUCTURE, Severity.ERROR, "a trade needs at least two teams")
        )
        fatal = True
    if len(set(ids)) != len(ids):
        findings.append(
            Finding(Rule.STRUCTURE, Severity.ERROR, f"duplicate team in participants: {ids}")
        )
        fatal = True

    known = set(ids)
    movements: Iterable[tuple[str, str, str]] = (
        [(p.player_id, p.from_team, p.to_team) for p in trade.players]
        + [(f"pick {p.draft_year} R{p.round}", p.from_team, p.to_team) for p in trade.picks]
        + [(f"cash ${c.amount:,}", c.from_team, c.to_team) for c in trade.cash]
    )
    for what, src, dst in movements:
        if src not in known or dst not in known:
            findings.append(
                Finding(
                    Rule.STRUCTURE,
                    Severity.ERROR,
                    f"{what} moves between non-participants ({src} -> {dst})",
                )
            )
            fatal = True
        elif src == dst:
            findings.append(
                Finding(Rule.STRUCTURE, Severity.ERROR, f"{what} moves from {src} to itself")
            )
            fatal = True

    if not trade.players and not trade.picks and not trade.cash:
        findings.append(Finding(Rule.STRUCTURE, Severity.ERROR, "trade moves no assets"))
        fatal = True
    return fatal


def _check_team(
    state: TeamTradeState,
    outgoing: list[PlayerAsset],
    incoming: list[PlayerAsset],
    trade: Trade,
    env: CapEnvironment,
    findings: list[Finding],
) -> TeamOutcome:
    out_total = sum(p.match_value_out for p in outgoing)

    # Players acquired under the minimum salary exception do not count as
    # incoming salary for matching. Apron status does not restrict this: the
    # 2023 CBA's apron rules bar the mid-level and bi-annual exceptions, not
    # the minimum. See README for the citation.
    minimum_players: list[PlayerAsset] = []
    matched_incoming: list[PlayerAsset] = []
    for candidate in incoming:
        try:
            # Cheap admissible bound first: a salary above the largest minimum
            # in any sourced season cannot be a minimum contract in an earlier,
            # smaller-cap season, so no scale lookup is needed and no
            # UNDETERMINED is raised. This is what keeps the pre-2023 legality
            # rate meaningful rather than uniformly undecidable.
            if cannot_be_a_minimum_contract(trade.season, candidate.salary):
                matched_incoming.append(candidate)
                continue
            qualifies = qualifies_for_minimum_exception(
                trade.season,
                candidate.salary,
                years_of_service=candidate.years_of_service,
                contract_years=candidate.contract_years,
                salary_exceeded_minimum_previously=candidate.salary_exceeded_minimum_previously,
            )
        except KeyError:
            # No minimum-salary scale is sourced for this season, and cap.py
            # raises rather than extrapolate from a neighbouring year - an
            # invented minimum would widen the exception and let unmatched
            # salary through. So the player is matched normally (the
            # conservative direction, since the exception only ever *waives*
            # matching) and the trade is flagged UNDETERMINED rather than
            # silently decided on a number we do not have.
            qualifies = False
            findings.append(
                Finding(
                    Rule.SALARY_MATCH,
                    Severity.UNDETERMINED,
                    (
                        f"no minimum-salary scale sourced for {trade.season}, so "
                        f"cannot tell whether {candidate.name} is a minimum-salary "
                        "player exempt from matching; matched him instead"
                    ),
                    team=state.team_id,
                    detail={"player": candidate.player_id,
                            "reason": "minimum scale not sourced"},
                )
            )
        bucket = minimum_players if qualifies else matched_incoming
        bucket.append(candidate)

    in_total = sum(p.salary for p in matched_incoming)
    # Team salary still rises by the full amount — the exception waives the
    # matching requirement, not the salary itself. Using in_total here would
    # understate payroll and could misclassify the team's apron tier.
    incoming_payroll = sum(p.salary for p in incoming)

    if minimum_players:
        findings.append(
            Finding(
                Rule.MINIMUM_SALARY_EXCEPTION,
                Severity.INFO,
                (
                    f"absorbs {len(minimum_players)} minimum-salary player(s) "
                    f"(${sum(p.salary for p in minimum_players):,}) outside salary matching"
                ),
                team=state.team_id,
                detail={"player_ids": [p.player_id for p in minimum_players]},
            )
        )

    tier_before = tier_for_salary(state.team_salary, env)
    salary_after = state.team_salary - out_total + incoming_payroll
    tier_after = tier_for_salary(salary_after, env)

    usable_tpes = _usable_trade_exceptions(state, trade.season, tier_after, env)
    tpe_capacity = sum(t.capacity() for t in usable_tpes)

    limit = max_incoming_salary(
        out_total, state.team_salary, env, post_trade_tier=tier_after
    )
    limit += tpe_capacity

    match = MatchOutcome(
        team_id=state.team_id,
        outgoing=out_total,
        incoming=in_total,
        salary_before=state.team_salary,
        tier_before=tier_before,
        tier_after=tier_after,
        max_incoming=limit,
    )

    if not match.legal:
        findings.append(
            Finding(
                Rule.SALARY_MATCH,
                Severity.ERROR,
                (
                    f"takes back ${in_total:,} against ${out_total:,} outgoing; "
                    f"limit is ${limit:,} ({tier_after.label} after the trade, "
                    f"{env.apron_match_pct}% apron matching in {env.season})"
                ),
                team=state.team_id,
                detail={
                    "outgoing": out_total,
                    "incoming": in_total,
                    "limit": limit,
                    "over_by": in_total - limit,
                    "tier_after": tier_after.name,
                },
            )
        )

    _check_aggregation(
        state, outgoing, matched_incoming, trade, env, tier_after, usable_tpes, findings
    )
    _check_base_year_compensation(state, outgoing, tier_before, findings)
    _check_player_eligibility(state, outgoing, trade, findings)
    _check_sign_and_trade(state, incoming, tier_after, findings)
    _check_roster(state, outgoing, incoming, findings)
    _check_hard_cap_and_floor(state, match, env, findings)

    return TeamOutcome(
        team_id=state.team_id,
        match=match,
        tier_before=tier_before,
        tier_after=tier_after,
        roster_after=state.roster_count - len(outgoing) + len(incoming),
        outgoing_players=outgoing,
        incoming_players=incoming,
    )


def _usable_trade_exceptions(
    state: TeamTradeState, season: str, tier_after: ApronTier,
    env: CapEnvironment,
) -> list[TradeException]:
    """Filter out exceptions this team is barred from using.

    Apron teams may not use a TPE generated in a prior league year, and
    second-apron teams may not use one generated by a sign-and-trade. The
    second restriction is 2023-CBA only, so it keys on ``env``.
    """
    usable = []
    for tpe in state.trade_exceptions:
        if tier_after >= ApronTier.FIRST_APRON and tpe.created_season != season:
            continue
        if (env.has_second_apron and tier_after >= ApronTier.SECOND_APRON
                and tpe.from_sign_and_trade):
            continue
        usable.append(tpe)
    return usable


def _check_aggregation(
    state: TeamTradeState,
    outgoing: list[PlayerAsset],
    incoming: list[PlayerAsset],
    trade: Trade,
    env: CapEnvironment,
    tier_after: ApronTier,
    usable_tpes: list[TradeException],
    findings: list[Finding],
) -> None:
    """Second-apron aggregation ban, and the 2-month post-acquisition window.

    "Aggregating" means combining two or more outgoing salaries to justify one
    incoming salary. Sending two players is only aggregation if the incoming
    salaries cannot be packed against the outgoing players individually.
    """
    if len(outgoing) < 2 or not incoming:
        return

    out_salaries = [p.match_value_out for p in outgoing]
    in_salaries = [p.salary for p in incoming]
    extra_bins = [t.capacity() for t in usable_tpes]

    # The apron tightens matching only under the 2023 CBA. Before it, an
    # over-the-apron team used the ordinary brackets like any other over-the-cap
    # team - the apron was a hard-cap trigger, not a matching restriction.
    if tier_after >= ApronTier.FIRST_APRON and env.apron_restricts_matching:
        def limit(salary: int) -> int:
            return pct_of(salary, env.apron_match_pct)
    else:
        def limit(salary: int) -> int:
            return exception_match_limit(salary, env)

    needs_aggregation = not can_fit_without_aggregating(
        out_salaries, in_salaries, env, per_player_limit=limit, extra_bins=extra_bins
    )

    if needs_aggregation and env.has_second_apron and tier_after >= ApronTier.SECOND_APRON:
        findings.append(
            Finding(
                Rule.AGGREGATION_SECOND_APRON,
                Severity.ERROR,
                (
                    f"aggregates {len(outgoing)} outgoing salaries while above the "
                    "second apron after the trade, which the 2023 CBA forbids outright"
                ),
                team=state.team_id,
                detail={"outgoing_salaries": out_salaries, "incoming_salaries": in_salaries},
            )
        )

    if not needs_aggregation:
        return

    cutoff = trade.trade_date
    for player in outgoing:
        acquired = player.acquired_via_trade_on
        if acquired is None:
            continue
        days = (cutoff - acquired).days
        if days < AGGREGATION_WINDOW_DAYS:
            findings.append(
                Finding(
                    Rule.AGGREGATION_WINDOW,
                    Severity.ERROR,
                    (
                        f"{player.name} was acquired by trade {days} days ago and cannot "
                        f"be aggregated with another player until "
                        f"{AGGREGATION_WINDOW_DAYS} days have passed"
                    ),
                    team=state.team_id,
                    detail={"player_id": player.player_id, "days_since_acquired": days},
                )
            )


def _check_base_year_compensation(
    state: TeamTradeState,
    outgoing: list[PlayerAsset],
    tier_before: ApronTier,
    findings: list[Finding],
) -> None:
    """Detect players who *might* be under base-year compensation. Never compute it.

    Under BYC a re-signed player's outgoing salary counts for his own team as
    the greater of his prior salary or 50% of his new salary — less than his
    cap hit. Computing that correctly needs his prior salary, which rights were
    used, and the exact expiry window, and getting any of it wrong produces a
    wrong verdict on a real trade. So this refuses to answer instead.

    Deliberately over-inclusive. We do not model when BYC status expires, so a
    player who satisfies the other preconditions is flagged regardless of how
    long ago he signed. Over-flagging costs an explicit "we don't know";
    under-flagging costs a silently wrong approval. Two escape hatches let a
    caller resolve the flag with knowledge we lack:

      * supply ``outgoing_match_value`` — asserts BYC is already accounted for;
      * supply ``previous_salary`` — a raise of 20% or less rules BYC out.
    """
    if tier_before is ApronTier.UNDER_CAP:
        return  # BYC only arises for a team over the cap at re-signing.

    for player in outgoing:
        if player.re_sign_status is ReSignStatus.NOT_RE_SIGNED:
            continue
        if player.outgoing_match_value is not None:
            continue  # caller asserts it has resolved the BYC value itself
        if player.previous_salary is not None and player.salary <= pct_of(
            player.previous_salary, 120
        ):
            continue  # raise of 20% or less cannot trigger BYC

        if player.re_sign_status is ReSignStatus.UNKNOWN:
            reason = (
                "we do not know whether he re-signed with this team, and the "
                "snapshot carries no re-sign status"
            )
        else:
            reason = "he re-signed with this team using Bird rights"

        findings.append(
            Finding(
                Rule.BASE_YEAR_COMPENSATION,
                Severity.UNDETERMINED,
                (
                    f"{player.name} may be a base-year-compensation player: {reason}, "
                    f"and the team is over the cap. His outgoing salary for matching "
                    f"could be less than his ${player.salary:,} cap hit. Supply "
                    f"previous_salary or outgoing_match_value to resolve this."
                ),
                team=state.team_id,
                detail={
                    "player_id": player.player_id,
                    "re_sign_status": player.re_sign_status.value,
                    "cap_hit": player.salary,
                },
            )
        )


def _check_player_eligibility(
    state: TeamTradeState,
    outgoing: list[PlayerAsset],
    trade: Trade,
    findings: list[Finding],
) -> None:
    for player in outgoing:
        restricted_until = player.trade_restricted_until
        if restricted_until is None and player.signed_on is not None:
            restricted_until = earliest_trade_date(player.signed_on)
        if restricted_until is not None and trade.trade_date < restricted_until:
            findings.append(
                Finding(
                    Rule.TRADE_RESTRICTION_WINDOW,
                    Severity.ERROR,
                    (
                        f"{player.name} cannot be traded until "
                        f"{restricted_until.isoformat()} (trade dated "
                        f"{trade.trade_date.isoformat()})"
                    ),
                    team=state.team_id,
                    detail={
                        "player_id": player.player_id,
                        "restricted_until": restricted_until.isoformat(),
                    },
                )
            )

        if player.no_trade_clause and not player.consent_given:
            findings.append(
                Finding(
                    Rule.NO_TRADE_CLAUSE,
                    Severity.ERROR,
                    f"{player.name} holds a no-trade clause and has not consented",
                    team=state.team_id,
                    detail={"player_id": player.player_id},
                )
            )


def earliest_trade_date(signed_on: date) -> date:
    """When a newly signed free agent first becomes trade-eligible.

    The later of 90 days after signing, or December 15 of that league year.
    Re-signings using Bird rights carry extra restrictions the caller can
    express directly via ``PlayerAsset.trade_restricted_until``.
    """
    from datetime import timedelta

    ninety_days = signed_on + timedelta(days=SIGNING_RESTRICTION_DAYS)
    month, day = SIGNING_RESTRICTION_MONTH_DAY
    # A league year runs July -> June, so a January signing's December 15 is
    # the one already behind it, not eleven months ahead.
    dec_year = signed_on.year if signed_on.month >= 7 else signed_on.year - 1
    dec_fifteen = date(dec_year, month, day)
    return max(ninety_days, dec_fifteen)


def _check_sign_and_trade(
    state: TeamTradeState,
    incoming: list[PlayerAsset],
    tier_after: ApronTier,
    findings: list[Finding],
) -> None:
    if tier_after < ApronTier.FIRST_APRON:
        return
    for player in incoming:
        if player.sign_and_trade:
            findings.append(
                Finding(
                    Rule.SIGN_AND_TRADE_APRON,
                    Severity.ERROR,
                    (
                        f"cannot acquire {player.name} via sign-and-trade while "
                        f"{tier_after.label} after the trade"
                    ),
                    team=state.team_id,
                    detail={"player_id": player.player_id},
                )
            )


def _check_roster(
    state: TeamTradeState,
    outgoing: list[PlayerAsset],
    incoming: list[PlayerAsset],
    findings: list[Finding],
) -> None:
    after = state.roster_count - len(outgoing) + len(incoming)
    if after > MAX_STANDARD_ROSTER:
        findings.append(
            Finding(
                Rule.ROSTER_LIMIT,
                Severity.ERROR,
                f"roster would be {after}, above the {MAX_STANDARD_ROSTER}-player maximum",
                team=state.team_id,
                detail={"roster_after": after},
            )
        )
    elif after < MIN_STANDARD_ROSTER:
        findings.append(
            Finding(
                Rule.ROSTER_MINIMUM,
                Severity.WARNING,
                (
                    f"roster would be {after}; the team must return to "
                    f"{MIN_STANDARD_ROSTER} within two weeks"
                ),
                team=state.team_id,
                detail={"roster_after": after},
            )
        )


def _check_hard_cap_and_floor(
    state: TeamTradeState,
    match: MatchOutcome,
    env: CapEnvironment,
    findings: list[Finding],
) -> None:
    if match.salary_after < env.minimum_team_salary:
        findings.append(
            Finding(
                Rule.MIN_TEAM_SALARY,
                Severity.WARNING,
                (
                    f"post-trade salary ${match.salary_after:,} is below the "
                    f"${env.minimum_team_salary:,} floor; the shortfall is owed to "
                    "the players' association at season's end"
                ),
                team=state.team_id,
                detail={"shortfall": env.minimum_team_salary - match.salary_after},
            )
        )

    # Below both aprons, taking back more than 110% *via the traded-player
    # exception* hard-caps you at the first apron for the rest of the league
    # year. Absorbing into cap room does not, so a team whose cap room alone
    # covers the incoming salary is untouched.
    room_limit = (
        max(0, env.salary_cap - state.team_salary) + match.outgoing + TRADE_CUSHION
    )
    if (
        match.tier_after is not None
        and match.tier_after < ApronTier.FIRST_APRON
        and match.outgoing > 0
        and match.incoming > room_limit
        and match.incoming > pct_of(match.outgoing, 110)
    ):
        findings.append(
            Finding(
                Rule.HARD_CAP,
                Severity.WARNING,
                (
                    f"takes back more than 110% of outgoing salary and is hard-capped "
                    f"at the first apron (${env.first_apron:,}) for the rest of {env.season}"
                ),
                team=state.team_id,
                detail={"hard_cap": env.first_apron},
            )
        )


def _check_cash(
    trade: Trade,
    per_team: dict[str, TeamOutcome],
    env: CapEnvironment,
    findings: list[Finding],
) -> None:
    for state in trade.teams:
        sent = sum(c.amount for c in trade.cash if c.from_team == state.team_id)
        received = sum(c.amount for c in trade.cash if c.to_team == state.team_id)
        outcome = per_team.get(state.team_id)
        tier_after = outcome.tier_after if outcome else ApronTier.UNDER_CAP

        # A prohibition, not a limit of zero. It is checked before and
        # independently of cash_limit so that it fires with its own rule id
        # however the annual limit moves, and so a GM agent reading the
        # findings learns "never" rather than "not this much".
        if sent and env.has_second_apron and tier_after >= ApronTier.SECOND_APRON:
            findings.append(
                Finding(
                    Rule.CASH_SECOND_APRON,
                    Severity.ERROR,
                    (
                        "cannot send cash in a trade while above the second apron; "
                        "this is an outright prohibition, not a reduced limit"
                    ),
                    team=state.team_id,
                    detail={"cash_sent": sent, "prohibition": True},
                )
            )

        for cash in trade.cash:
            if cash.from_team == state.team_id and 0 < cash.amount < MIN_CASH_IN_TRADE:
                findings.append(
                    Finding(
                        Rule.CASH_MINIMUM,
                        Severity.ERROR,
                        (
                            f"cash consideration of ${cash.amount:,} is below the "
                            f"${MIN_CASH_IN_TRADE:,} minimum"
                        ),
                        team=state.team_id,
                        detail={"amount": cash.amount},
                    )
                )

        total_sent = state.cash_sent_this_year + sent
        total_received = state.cash_received_this_year + received

        # A cash limit of 0 means "not sourced for this season", not "no cash
        # may move". The distinction is the whole point: treating an unsourced
        # figure as a limit of zero would reject every pre-2023 trade with cash
        # in it, and the rejection would read as a rule finding rather than as
        # a gap in the data. UNDETERMINED says what is actually true.
        if not env.cash_limit:
            if sent or received:
                findings.append(
                    Finding(
                        Rule.CASH_LIMIT,
                        Severity.UNDETERMINED,
                        (
                            f"cash moved, but no cash limit is sourced for "
                            f"{env.season}; cannot rule on it"
                        ),
                        team=state.team_id,
                        detail={"sent": sent, "received": received,
                                "reason": "limit not sourced"},
                    )
                )
            continue

        for direction, total in (("sent", total_sent), ("received", total_received)):
            if total > env.cash_limit:
                findings.append(
                    Finding(
                        Rule.CASH_LIMIT,
                        Severity.ERROR,
                        (
                            f"would have {direction} ${total:,} in cash this league year, "
                            f"above the ${env.cash_limit:,} limit"
                        ),
                        team=state.team_id,
                        detail={"direction": direction, "total": total},
                    )
                )


def _check_picks(trade: Trade, findings: list[Finding]) -> None:
    """Stepien rule: no team may be left without a first-rounder two years running."""
    first_rounders = [p for p in trade.picks if p.round == 1]
    if not first_rounders:
        return

    for state in trade.teams:
        owned = state.picks_by_year()
        if not owned:
            continue  # caller did not supply pick inventory; nothing to check
        for pick in first_rounders:
            if pick.from_team == state.team_id:
                owned[pick.draft_year] = owned.get(pick.draft_year, 0) - 1
            elif pick.to_team == state.team_id:
                owned[pick.draft_year] = owned.get(pick.draft_year, 0) + 1

        years = sorted(owned)
        horizon = years[:STEPIEN_HORIZON_YEARS]
        for earlier, later in zip(horizon, horizon[1:]):
            if later != earlier + 1:
                continue
            if owned[earlier] <= 0 and owned[later] <= 0:
                findings.append(
                    Finding(
                        Rule.STEPIEN,
                        Severity.ERROR,
                        (
                            f"would leave {state.team_id} without a first-round pick in "
                            f"both {earlier} and {later}, violating the Stepien rule"
                        ),
                        team=state.team_id,
                        detail={"years": [earlier, later]},
                    )
                )
                break


def summarize(validation: TradeValidation) -> str:
    """One-screen human summary — used by the CLI and the event log."""
    lines = [f"{validation.verdict.name} ({validation.season})"]
    for team_id, outcome in sorted(validation.per_team.items()):
        m = outcome.match
        lines.append(
            f"  {team_id}: out ${m.outgoing:,} / in ${m.incoming:,} "
            f"(limit ${m.max_incoming:,}, headroom ${m.headroom:,}) "
            f"{outcome.tier_before.name} -> {outcome.tier_after.name}"
        )
    for finding in validation.findings:
        lines.append(f"  {finding}")
    return "\n".join(lines)
