"""Cap arithmetic: apron classification and salary-matching limits.

This module answers two questions and nothing else:

  1. Which apron tier is a team in at a given salary?
  2. Given an outgoing salary, how much may that team take back?

The trade validator composes these; keeping them separate means the matching
formula can be unit-tested against hand-computed values without constructing a
whole trade.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

from mironba.rules.constants import (
    MAX_MINIMUM_EXCEPTION_CONTRACT_YEARS,
    MINIMUM_SALARY_SCALE,
    SMALL_SALARY_MATCH_PCT,
    STANDARD_MATCH_PCT,
    TRADE_CUSHION,
    CapEnvironment,
)


class ApronTier(IntEnum):
    """Where a team sits relative to the cap and the two aprons.

    Ordered so that ``tier >= ApronTier.FIRST_APRON`` is a meaningful test for
    "subject to apron restrictions".
    """

    UNDER_CAP = 0
    OVER_CAP = 1  # over the cap, below the first apron
    FIRST_APRON = 2  # at/above the first apron, below the second
    SECOND_APRON = 3  # at/above the second apron

    @property
    def label(self) -> str:
        return {
            ApronTier.UNDER_CAP: "under the cap",
            ApronTier.OVER_CAP: "over the cap, below the first apron",
            ApronTier.FIRST_APRON: "above the first apron",
            ApronTier.SECOND_APRON: "above the second apron",
        }[self]


def tier_for_salary(team_salary: int, env: CapEnvironment) -> ApronTier:
    """Classify a team salary.

    The aprons are *hard lines*: a team exactly at the apron is treated as
    above it, matching how the league applies apron restrictions.
    """
    # No second apron before the 2023 CBA. Returning SECOND_APRON for a 2019
    # team would apply restrictions that did not exist, and would reject
    # trades the league actually approved.
    if env.has_second_apron and team_salary >= env.second_apron:
        return ApronTier.SECOND_APRON
    if team_salary >= env.first_apron:
        return ApronTier.FIRST_APRON
    if team_salary > env.salary_cap:
        return ApronTier.OVER_CAP
    return ApronTier.UNDER_CAP


def pct_of(amount: int, percent: int) -> int:
    """``percent``% of ``amount``, in whole dollars, rounded down.

    Integer-only so results are bit-identical across platforms.
    """
    return (amount * percent) // 100


def exception_match_limit(outgoing: int, env: CapEnvironment) -> int:
    """Most salary a below-apron, over-the-cap team may take back for ``outgoing``.

    The CBA states this as three brackets keyed on the outgoing amount:
    200% + $250K for small salaries, ``outgoing`` + the expanded TPE in the
    middle, 125% + $250K for large ones. The middle bracket is defined to apply
    only when it lands *between* the other two, which makes the whole table the
    **median** of the three formulas — not the maximum. Computing it that way
    means the bracket boundaries move correctly on their own as the expanded
    TPE scales with the cap each season, instead of being hardcoded and going
    stale.

    Sanity check on the derivation: with a $7.5M expanded TPE the upper
    crossover falls at exactly $29,000,000, which is the published boundary.
    The lower crossover computes to $7.25M against a published "$7.5M"; the
    published figure appears to be the expanded-TPE amount reused as a
    round-number label. The gap only matters for outgoing salaries in
    $7.25M-$7.50M, where this returns the more conservative bracket.
    """
    brackets = env.match_brackets
    cushion = brackets["cushion"]
    candidates = sorted(
        (
            pct_of(outgoing, brackets["small_pct"]) + cushion,
            outgoing + env.middle_buffer,
            pct_of(outgoing, brackets["large_pct"]) + cushion,
        )
    )
    return candidates[1]


def max_incoming_salary(
    outgoing: int,
    team_salary_before: int,
    env: CapEnvironment,
    *,
    post_trade_tier: ApronTier | None = None,
) -> int:
    """Most incoming salary this team may legally absorb.

    ``post_trade_tier`` is the tier the team lands in *after* the trade — the
    CBA applies apron restrictions based on where a team ends up, not where it
    started. When omitted it is solved for: the tier depends on the incoming
    salary, which is what we are bounding, so we take the most permissive tier
    that is self-consistent.
    """
    if post_trade_tier is None:
        post_trade_tier = _self_consistent_tier(outgoing, team_salary_before, env)

    if post_trade_tier >= ApronTier.FIRST_APRON and env.apron_restricts_matching:
        # Apron teams get no cushion and no brackets: a flat share of outgoing.
        # 2023 CBA only - see CapEnvironment.apron_restricts_matching.
        return pct_of(outgoing, env.apron_match_pct)

    # Below the aprons a team may either absorb into cap room or use the
    # traded-player exception brackets, whichever is more permissive.
    cap_room = max(0, env.salary_cap - team_salary_before)
    room_limit = cap_room + outgoing + TRADE_CUSHION
    return max(room_limit, exception_match_limit(outgoing, env))


def matching_upper_bound(
    outgoing: int, team_salary_before: int, env: CapEnvironment
) -> int:
    """Loosest limit *any* post-trade tier could grant. For pruning only.

    ``max_incoming_salary`` with no ``post_trade_tier`` answers a different
    question — "what can this team be sure of?" — and answers it conservatively,
    because ``_self_consistent_tier`` must pick one tier and a tier that would
    be crossed is not self-consistent. Golden State at $176.5M sending $8M gets
    $8,000,000 from it, since taking the $15.75M bracket amount would push them
    over the first apron. The true maximum is $9,591,056: enough to land one
    dollar below the apron, where the bracket still applies.

    That gap is harmless in ``validate_trade``, which knows the actual incoming
    salary and passes the resulting tier explicitly. It was not harmless as a
    search prune. ``solve`` used the conservative figure to discard subsets
    before validating them, so it threw away packages the validator would have
    approved — twelve of them for the Lakers, which is the whole reason that
    scenario reported no acquirable target at all.

    A prune may over-admit; the full validator is right behind it and the only
    cost is time. A prune may never under-admit, because nothing runs behind it
    to catch what it dropped. Taking the maximum over every tier makes the
    bound sound by construction: whatever tier the trade actually lands in,
    this is at least that tier's limit.
    """
    return max(
        max_incoming_salary(outgoing, team_salary_before, env, post_trade_tier=tier)
        for tier in ApronTier
    )


def _self_consistent_tier(
    outgoing: int, team_salary_before: int, env: CapEnvironment
) -> ApronTier:
    """Find the post-trade tier a team can actually reach.

    Try tiers from most to least permissive. A tier is self-consistent if
    taking back the maximum that tier allows actually leaves the team in that
    tier (or lower). This resolves the circularity between "how much can you
    take back" and "which tier are you in afterwards".
    """
    # Every tier, most permissive first. OVER_CAP was missing here, and its
    # absence was not a cosmetic gap: a team over the cap but below the first
    # apron — the most common situation in the league — fell through to the
    # FIRST_APRON branch and got flat 100% apron matching instead of the
    # bracket table. At $20M outgoing that is $20M back rather than $27.75M,
    # so the validator rejected trades the CBA plainly allows. Found by the
    # solver, which could not construct a legal package for an ordinary
    # over-cap team and made the discrepancy impossible to miss.
    for tier in (
        ApronTier.UNDER_CAP,
        ApronTier.OVER_CAP,
        ApronTier.FIRST_APRON,
        ApronTier.SECOND_APRON,
    ):
        limit = max_incoming_salary(
            outgoing, team_salary_before, env, post_trade_tier=tier
        )
        if tier_for_salary(team_salary_before - outgoing + limit, env) <= tier:
            return tier
    return ApronTier.SECOND_APRON


def minimum_salary(season: str, years_of_service: int) -> int:
    """Minimum salary for a player with ``years_of_service`` years, in ``season``.

    Raises for a season with no sourced scale rather than extrapolating from a
    neighbouring year — an invented minimum would silently widen the
    minimum-salary exception and let illegal salary through unmatched.
    """
    try:
        scale = MINIMUM_SALARY_SCALE[season]
    except KeyError:
        known = ", ".join(sorted(MINIMUM_SALARY_SCALE))
        raise KeyError(
            f"no minimum salary scale for {season!r}; sourced seasons: {known}"
        ) from None
    return scale[min(max(years_of_service, 0), 10)]


def cannot_be_a_minimum_contract(season: str, salary: int) -> bool:
    """True when ``salary`` is too large to be a minimum in ``season``, for sure.

    An admissible bound, and it exists so an unsourced minimum-salary scale does
    not make every pre-2023 trade UNDETERMINED. The scale tracks the cap, and
    every pre-2023 cap is below every sourced one, so the largest minimum in any
    *sourced* season is an upper bound for every earlier season. A salary above
    it cannot be a minimum contract in an earlier year, whatever the scale said.

    The bound may over-admit - it never under-admits. A salary below it falls
    through to the real scale, and if that is missing the caller reports
    UNDETERMINED rather than guessing.
    """
    if season in MINIMUM_SALARY_SCALE:
        return False
    if int(season[:4]) >= min(int(s[:4]) for s in MINIMUM_SALARY_SCALE):
        # Not an earlier season, so the bound argument does not apply.
        return False
    ceiling = max(max(tiers.values()) for tiers in MINIMUM_SALARY_SCALE.values())
    return salary > ceiling


def qualifies_for_minimum_exception(
    season: str,
    salary: int,
    *,
    years_of_service: int | None,
    contract_years: int | None = None,
    salary_exceeded_minimum_previously: bool = False,
) -> bool:
    """Whether an incoming player may be absorbed by the minimum salary exception.

    A qualifying player does not count as incoming salary for matching, which
    is why the conditions are applied strictly:

      * salary at or below the minimum for his experience;
      * contract no longer than two years;
      * salary never above the minimum in an earlier year of the contract.

    When ``years_of_service`` is unknown we fall back to the *zero-experience*
    minimum, the lowest rung. That grants the exception only when it is certain
    regardless of experience. The failure mode is refusing the exception to a
    player who deserved it — which over-counts incoming salary and risks
    rejecting a legal trade, never approving an illegal one.
    """
    if contract_years is not None and contract_years > MAX_MINIMUM_EXCEPTION_CONTRACT_YEARS:
        return False
    if salary_exceeded_minimum_previously:
        return False
    threshold = minimum_salary(season, years_of_service if years_of_service is not None else 0)
    return salary <= threshold


@dataclass(frozen=True, slots=True)
class TeamSalaryState:
    """A team's cap position at a point in time.

    ``team_salary`` is total team salary for cap purposes: guaranteed cap hits
    of everyone on the standard roster, plus dead money and cap holds. It is
    supplied by the caller rather than recomputed here so that the same rules
    engine works against a live roster, a historical snapshot, or a
    hypothetical world state produced by the simulator.
    """

    team_id: str
    season: str
    team_salary: int
    roster_count: int
    dead_money: int = 0
    two_way_count: int = 0

    def tier(self, env: CapEnvironment) -> ApronTier:
        return tier_for_salary(self.team_salary, env)

    def cap_room(self, env: CapEnvironment) -> int:
        return max(0, env.salary_cap - self.team_salary)

    def over_tax_by(self, env: CapEnvironment) -> int:
        return max(0, self.team_salary - env.tax_level)


@dataclass(frozen=True, slots=True)
class TradeException:
    """A traded-player exception a team may use to absorb incoming salary.

    ``created_season`` matters: apron teams may not use a TPE generated in a
    prior league year.
    """

    amount: int
    created_season: str
    label: str = ""
    from_sign_and_trade: bool = False

    def capacity(self) -> int:
        return self.amount + TRADE_CUSHION


def can_fit_without_aggregating(
    outgoing_salaries: Sequence[int],
    incoming_salaries: Sequence[int],
    env: CapEnvironment,
    *,
    per_player_limit: Callable[[int], int] | None = None,
    extra_bins: Sequence[int] = (),
) -> bool:
    """Can every incoming player be matched without combining outgoing salaries?

    Each outgoing player is a bin whose capacity is the most salary that player
    alone can absorb; each trade exception is an extra bin. The question is
    whether the incoming players can be packed into those bins. If they cannot,
    the trade *requires aggregation* — which is banned outright for
    second-apron teams and banned for recently-acquired players.

    Exact backtracking. Rosters cap trade sizes in the single digits, so the
    exponential worst case never materialises; a heuristic here would produce
    wrong legality verdicts, which is exactly what this package exists to
    prevent.
    """
    if per_player_limit is None:

        def per_player_limit(salary: int) -> int:
            return exception_match_limit(salary, env)

    bins = [per_player_limit(s) for s in outgoing_salaries] + list(extra_bins)
    if not incoming_salaries:
        return True
    if not bins:
        return False

    # Largest-first ordering prunes hopeless branches almost immediately.
    items = sorted(incoming_salaries, reverse=True)
    remaining = sorted(bins, reverse=True)

    def place(index: int, capacities: list[int]) -> bool:
        if index == len(items):
            return True
        item = items[index]
        tried: set[int] = set()
        for i, cap in enumerate(capacities):
            if cap < item or cap in tried:
                continue  # equal capacities are interchangeable; try each once
            tried.add(cap)
            nxt = list(capacities)
            nxt[i] = cap - item
            if place(index + 1, nxt):
                return True
        return False

    return place(0, remaining)


@dataclass
class MatchOutcome:
    """Result of the salary-matching test for one team in one trade."""

    team_id: str
    outgoing: int
    incoming: int
    salary_before: int
    salary_after: int = field(init=False)
    tier_before: ApronTier | None = None
    tier_after: ApronTier | None = None
    max_incoming: int = 0

    def __post_init__(self) -> None:
        self.salary_after = self.salary_before - self.outgoing + self.incoming

    @property
    def legal(self) -> bool:
        return self.incoming <= self.max_incoming

    @property
    def headroom(self) -> int:
        """Dollars of additional incoming salary still available."""
        return self.max_incoming - self.incoming
