"""When a newly signed player becomes trade-eligible.

No offseason scenario is correct without this. A summer signing is not a
tradeable asset in October, and a simulator that does not know it will happily
build packages out of players nobody may legally move — the same class of error
as ignoring salary matching, and less visible because the arithmetic all works.

The rule, from the CBA's restrictions on newly signed free agents:

  A free agent who signs a contract may not be traded for **three months** from
  the date of signing, or until **December 15** of the salary-cap year in which
  he signed, **whichever is later**.

  Extended case: if the team is over the cap immediately after signing, the
  contract was made with the **Bird or Early Bird** exception, and first-year
  salary exceeds **120%** of the player's prior-season salary, the date moves
  to the later of three months and **January 15**.

Sources, both retrieved 2026-07-31:

  * NBA.com, "NBA trade deadline explained"
    https://api-hub.nba.com/news/nba-trade-deadline-explained
  * Hoops Rumors, "Free Agents Who Sign After Monday Won't Be Trade-Eligible On
    December 15" — states the three-months-or-December-15 rule and works the
    boundary case where a September signing pushes past the 15th.
    https://www.hoopsrumors.com/2025/09/free-agents-who-sign-after-monday-wont-be-trade-eligible-on-december-15.html

What is deliberately NOT modelled here: the separate restriction on players
acquired by trade, sign-and-trade rules, and the two-month re-acquisition bar
on a waived player. Each is a different rule with a different clock, and
guessing at one would put a wrong date in the same field as a sourced one.
``rules/`` may say "I do not know" — it may not improvise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

from mironba.rules.constants import MAX_STANDARD_ROSTER

#: The two fixed dates the rule can land on, as (month, day).
STANDARD_UNLOCK = (12, 15)
EXTENDED_UNLOCK = (1, 15)

#: The raise that moves a Bird/Early Bird re-signing to the January date.
EXTENDED_RAISE_THRESHOLD = 1.20

#: Months of mandatory restriction from the signing date, in both cases.
RESTRICTION_MONTHS = 3


class SigningRuleError(ValueError):
    """The inputs cannot decide an answer. Never defaulted to 'allowed'."""


def add_months(start: date, months: int) -> date:
    """Calendar months, clamped to the end of a short month.

    "Three months after November 30" has no 31st to land on, so it lands on
    the 28th/29th. Adding 90 days instead would be a different rule and would
    disagree with the CBA by up to two days around February.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year + month // 12, month % 12 + 1, 1) - date(year, month, 1)).days


def cap_year_start(signed_on: date) -> int:
    """The year the salary-cap year containing ``signed_on`` began.

    Cap years run July 1 to June 30, so a January signing belongs to the cap
    year that started the previous July. Getting this wrong would put the
    December 15 unlock eleven months in the wrong direction, and it would look
    plausible either way.
    """
    return signed_on.year if signed_on.month >= 7 else signed_on.year - 1


@dataclass(frozen=True, slots=True)
class SigningRestriction:
    """When this contract becomes tradeable, and which branch decided it."""

    trade_eligible_on: date
    rule: str
    three_month_date: date
    fixed_date: date

    def tradeable_on(self, when: date) -> bool:
        return when >= self.trade_eligible_on

    def explain(self) -> str:
        return (
            f"tradeable from {self.trade_eligible_on.isoformat()} "
            f"({self.rule}: later of {self.three_month_date.isoformat()} and "
            f"{self.fixed_date.isoformat()})"
        )


def signing_restriction(
    signed_on: date,
    *,
    bird_or_early_bird: bool = False,
    over_cap_after_signing: bool = False,
    first_year_salary: int | None = None,
    prior_season_salary: int | None = None,
) -> SigningRestriction:
    """Date this newly signed free agent may first be traded.

    The extended branch needs all three of its conditions, and it needs both
    salaries to test the third. Asking for the extended branch without the
    figures raises rather than silently falling back to December — the fallback
    would be the permissive answer, and a permissive guess here approves a
    trade the league would void.
    """
    three_month = add_months(signed_on, RESTRICTION_MONTHS)
    year = cap_year_start(signed_on)

    extended = False
    if bird_or_early_bird and over_cap_after_signing:
        if first_year_salary is None or prior_season_salary is None:
            raise SigningRuleError(
                "a Bird/Early Bird re-signing by an over-cap team needs both "
                "first_year_salary and prior_season_salary to decide between "
                "the December 15 and January 15 unlock; got "
                f"{first_year_salary!r} and {prior_season_salary!r}"
            )
        # Strictly greater: the CBA says "in excess of 120%", so a contract at
        # exactly 120% takes the December date.
        extended = first_year_salary > EXTENDED_RAISE_THRESHOLD * prior_season_salary

    if extended:
        fixed = date(year + 1, *EXTENDED_UNLOCK)
        rule = "Bird/Early Bird re-signing over the cap above 120%"
    else:
        fixed = date(year, *STANDARD_UNLOCK)
        rule = "standard free-agent signing"

    return SigningRestriction(
        trade_eligible_on=max(three_month, fixed),
        rule=rule,
        three_month_date=three_month,
        fixed_date=fixed,
    )


# --------------------------------------------------------------------------
# Signing routes
#
# The trade solver's counterpart. A trade asks "which packages balance"; a
# signing asks "which exception can pay for this player, and how much may it
# pay". Same shape of answer: a set of legal options, or an explicit account of
# why there are none.
#
# Every dollar figure comes from `rules/constants.py` and carries a provenance
# entry there. Every *rule* below cites its source in the docstring. Anything
# not modelled is named in NOT_MODELLED rather than silently approximated,
# because an unmodelled restriction reads as permission.
#
# Sources, all retrieved 2026-07-31:
#   Hoops Rumors, "Values Of 2026/27 Mid-Level, Bi-Annual Exceptions"
#     https://www.hoopsrumors.com/2026/07/values-of-2026-27-mid-level-bi-annual-exceptions.html
#     - first-year amounts, contract lengths, raise percentages, and which
#       teams are eligible for each exception
#   Hoops Rumors, "Hoops Rumors Glossary: Bi-Annual Exception"
#     https://www.hoopsrumors.com/2026/03/hoops-rumors-glossary-bi-annual-exception-6.html
#   NBA.com, "NBA trade deadline explained"
#     https://api-hub.nba.com/news/nba-trade-deadline-explained
# --------------------------------------------------------------------------

CAP_SPACE = "cap_space"
BIRD = "bird"
EARLY_BIRD = "early_bird"
NON_BIRD = "non_bird"
NON_TAXPAYER_MLE = "non_taxpayer_mle"
TAXPAYER_MLE = "taxpayer_mle"
ROOM_EXCEPTION = "room_exception"
BI_ANNUAL = "bi_annual"
MINIMUM = "minimum"

#: Ordered most to least valuable, which is also the order a GM would consider
#: them. Used for deterministic output ordering, never for choosing.
ROUTES = (
    CAP_SPACE, BIRD, NON_TAXPAYER_MLE, EARLY_BIRD, ROOM_EXCEPTION,
    TAXPAYER_MLE, BI_ANNUAL, NON_BIRD, MINIMUM,
)

#: Maximum salary as a share of the cap, by years of service. The three tiers
#: of the 2023 CBA.
MAX_SALARY_TIERS = ((0, 25), (7, 30), (10, 35))

#: Early Bird: 175% of the prior salary. The CBA also allows 105% of the
#: *league average salary* where that is larger, and league average is not in
#: any table this project ingests — see NOT_MODELLED.
EARLY_BIRD_RAISE_PCT = 175

#: Non-Bird: 120% of the prior salary.
NON_BIRD_RAISE_PCT = 120

#: Per-exception contract limits, from the Hoops Rumors table above.
ROUTE_TERMS: dict[str, tuple[int, int]] = {
    #  route            -> (max years, annual raise %)
    NON_TAXPAYER_MLE: (4, 5),
    TAXPAYER_MLE: (2, 5),
    ROOM_EXCEPTION: (3, 5),
    BI_ANNUAL: (2, 5),
    MINIMUM: (2, 5),
    CAP_SPACE: (4, 5),
    BIRD: (5, 8),
    EARLY_BIRD: (4, 8),
    NON_BIRD: (4, 5),
}

#: Which exceptions put a hard cap on the team that uses them, and where.
#: A hard cap is not a tax line — it cannot be exceeded for any reason for the
#: rest of the league year, so it constrains every later move.
HARD_CAP_TRIGGERS: dict[str, str] = {
    NON_TAXPAYER_MLE: "first_apron",
    BI_ANNUAL: "first_apron",
    TAXPAYER_MLE: "second_apron",
}

#: Named so that an unmodelled rule cannot be mistaken for an absent one.
#: Every entry here is a way this module may say "legal" when the league would
#: not, which is the dangerous direction.
NOT_MODELLED = (
    "Early Bird's alternative ceiling of 105% of the league average salary — "
    "league average is not in any ingested table, so only the 175%-of-prior "
    "branch is offered and Early Bird is understated for cheap incumbents.",
    "Sign-and-trade, which has its own hard cap and its own matching rules.",
    "Rookie-scale contracts and their option years.",
    "Restricted free agency, offer sheets, and the right of first refusal.",
    "Renouncing cap holds: cap room is taken as given rather than derived from "
    "which holds a team chooses to keep.",
    "Two-way contracts and Exhibit 10 deals.",
    "The over-38 rule and other contract-structure restrictions.",
)


def max_salary(season_cap: int, years_of_service: int) -> int:
    """Maximum first-year salary for a player with this much service."""
    pct = MAX_SALARY_TIERS[0][1]
    for threshold, value in MAX_SALARY_TIERS:
        if years_of_service >= threshold:
            pct = value
    return (season_cap * pct) // 100


@dataclass(frozen=True, slots=True)
class FreeAgent:
    """A player available to sign, and what rights the team holds on him."""

    player_id: str
    name: str
    years_of_service: int
    #: Prior-season salary. Drives the Early Bird and Non-Bird ceilings, and is
    #: never shown to a model.
    prior_salary: int = 0
    #: Consecutive seasons finished with *this* team without changing teams as
    #: a free agent. 3+ is Bird, 2 is Early Bird, 1 is Non-Bird, 0 is none.
    years_with_team: int = 0

    @property
    def rights(self) -> str | None:
        if self.years_with_team >= 3:
            return BIRD
        if self.years_with_team == 2:
            return EARLY_BIRD
        if self.years_with_team == 1:
            return NON_BIRD
        return None


@dataclass(frozen=True, slots=True)
class TeamCapState:
    """A team's signing position. Salary figures are inputs, not derived.

    ``committed_salary`` excludes the signing being considered and includes
    everything else already on the books. ``cap_holds`` is supplied rather than
    computed because which holds a team keeps is a decision, not a fact — see
    NOT_MODELLED.
    """

    team_id: str
    season: str
    committed_salary: int
    cap_holds: int = 0
    roster_count: int = 0
    #: Exceptions already spent this league year.
    non_taxpayer_mle_used: int = 0
    taxpayer_mle_used: int = 0
    room_exception_used: int = 0
    bi_annual_used: int = 0
    #: The bi-annual is available once every two years.
    bi_annual_used_prior_season: bool = False
    #: True once the team has gone under the cap to sign someone. A team gets
    #: cap room *or* the mid-level, never both, and this is what records which
    #: side of that fork it took.
    used_cap_space: bool = False

    def cap_room(self, env) -> int:
        return max(0, env.salary_cap - self.committed_salary - self.cap_holds)

    def tier_after(self, incoming: int, env) -> str:
        total = self.committed_salary + incoming
        if total >= env.second_apron:
            return "second_apron"
        if total >= env.first_apron:
            return "first_apron"
        if total > env.salary_cap:
            return "over_cap"
        return "under_cap"


@dataclass(frozen=True, slots=True)
class SigningRoute:
    """One legal way to pay a player, and the terms it permits."""

    route: str
    max_first_year: int
    max_years: int
    raise_pct: int
    hard_cap: str | None = None
    note: str = ""

    def total_value(self) -> int:
        """Full value of a maximum-length deal at this route's raises."""
        total, salary = 0, self.max_first_year
        for _ in range(self.max_years):
            total += salary
            salary += (self.max_first_year * self.raise_pct) // 100
        return total

    def describe(self) -> str:
        cap = f", hard-caps at the {self.hard_cap.replace('_', ' ')}" if self.hard_cap else ""
        return (
            f"{self.route}: up to ${self.max_first_year:,} in year one, "
            f"{self.max_years} year(s), {self.raise_pct}% raises{cap}"
        )


@dataclass
class SigningResult:
    """Legal routes, or an explicit account of why there are none."""

    player_id: str = ""
    routes: list[SigningRoute] = field(default_factory=list)
    #: route -> why it is unavailable. The interesting half when nothing works.
    blocked: dict[str, str] = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def any_route(self) -> bool:
        return bool(self.routes)

    @property
    def max_first_year(self) -> int:
        return max((r.max_first_year for r in self.routes), default=0)

    def route_names(self) -> tuple[str, ...]:
        return tuple(r.route for r in self.routes)

    def best(self) -> SigningRoute | None:
        """Highest first-year salary, ties broken by the ROUTES ordering."""
        if not self.routes:
            return None
        return max(
            self.routes,
            key=lambda r: (r.max_first_year, -ROUTES.index(r.route)),
        )

    def explain(self) -> str:
        if self.routes:
            return f"{len(self.routes)} route(s): " + "; ".join(
                r.describe() for r in self.routes
            )
        if not self.blocked:
            return "no route, and no rule was consulted — this is a bug"
        return "no legal route. " + " ".join(
            f"{route}: {why}." for route, why in sorted(self.blocked.items())
        )


def signing_routes(
    team: TeamCapState,
    player: FreeAgent,
    env,
    *,
    minimum_salary_fn=None,
) -> SigningResult:
    """Every legal way this team may sign this player, with maximum terms.

    The mirror of ``rules/solver.solve``: the deterministic layer enumerates
    what is legal so that nothing upstream has to guess. Each route records why
    it was refused when it does not apply, because "no legal route" is not
    actionable and "you are above the first apron, so the mid-level is gone" is.
    """
    from mironba.rules.cap import minimum_salary

    minimum_salary_fn = minimum_salary_fn or minimum_salary
    started = time.monotonic()
    result = SigningResult(player_id=player.player_id)
    room = team.cap_room(env)
    over_cap = team.committed_salary > env.salary_cap
    tier = team.tier_after(0, env)
    ceiling = max_salary(env.salary_cap, player.years_of_service)

    def allow(route: str, amount: int, note: str = "") -> None:
        years, raise_pct = ROUTE_TERMS[route]
        amount = min(amount, ceiling)
        hard_cap = HARD_CAP_TRIGGERS.get(route)
        if hard_cap is not None:
            # A hard cap is not a flag to record alongside the amount — it
            # limits the amount. Using the non-taxpayer mid-level hard-caps the
            # team at the first apron for the rest of the league year, so a
            # team $5.9M below that line may use $5.9M of a $15.0M exception
            # and no more. Reporting the headline figure would have offered
            # Golden State $15,044,000 at $203.5M committed, which lands at
            # $218.6M against a $209.0M hard cap.
            line = env.first_apron if hard_cap == "first_apron" else env.second_apron
            headroom = line - team.committed_salary
            if headroom <= 0:
                block(route, f"using it hard-caps at the {hard_cap.replace('_', ' ')}, "
                             f"which this team is already past")
                return
            if headroom < amount:
                note = (note + "; " if note else "") + (
                    f"limited to ${headroom:,} by the "
                    f"{hard_cap.replace('_', ' ')} hard cap it triggers"
                )
                amount = headroom
        result.routes.append(
            SigningRoute(
                route=route,
                max_first_year=amount,
                max_years=years,
                raise_pct=raise_pct,
                hard_cap=hard_cap,
                note=note,
            )
        )

    def block(route: str, why: str) -> None:
        result.blocked[route] = why

    # -- cap space ------------------------------------------------------
    if room > 0:
        allow(CAP_SPACE, room, "signs into room rather than an exception")
    else:
        block(CAP_SPACE, f"no cap room (committed ${team.committed_salary:,} "
                         f"against a ${env.salary_cap:,} cap)")

    # -- room exception -------------------------------------------------
    # Only for a team that used its cap room. The fork is the point: a team
    # takes room *or* the mid-level, and the room exception is the smaller
    # consolation the room side gets.
    if team.used_cap_space or room > 0:
        remaining = env.room_exception - team.room_exception_used
        if remaining > 0:
            allow(ROOM_EXCEPTION, remaining)
        else:
            block(ROOM_EXCEPTION, "already spent")
    else:
        block(ROOM_EXCEPTION, "only available to a team that used cap room")

    # -- mid-level exceptions -------------------------------------------
    # Which mid-level a team gets is decided by where it sits, and the aprons
    # remove them one at a time.
    if room > 0 and not team.used_cap_space:
        block(NON_TAXPAYER_MLE,
              "a team with cap room gets the room exception instead")
    elif tier == "second_apron":
        block(NON_TAXPAYER_MLE, "above the second apron")
    elif tier == "first_apron":
        block(NON_TAXPAYER_MLE, "above the first apron")
    else:
        remaining = env.non_taxpayer_mle - team.non_taxpayer_mle_used
        if remaining > 0:
            allow(NON_TAXPAYER_MLE, remaining)
        else:
            block(NON_TAXPAYER_MLE, "already spent")

    if tier == "second_apron":
        block(TAXPAYER_MLE, "above the second apron: no mid-level at all")
    elif not over_cap and room > 0:
        block(TAXPAYER_MLE, "under the cap; use room or the room exception")
    else:
        remaining = env.taxpayer_mle - team.taxpayer_mle_used
        if remaining > 0:
            allow(TAXPAYER_MLE, remaining)
        else:
            block(TAXPAYER_MLE, "already spent")

    # -- bi-annual ------------------------------------------------------
    if team.bi_annual_used_prior_season:
        block(BI_ANNUAL, "used last season; it is available once every two years")
    elif tier in ("first_apron", "second_apron"):
        block(BI_ANNUAL, "above the first apron")
    elif room > 0 and not team.used_cap_space:
        block(BI_ANNUAL, "a team with cap room does not carry the bi-annual")
    else:
        remaining = env.bi_annual_exception - team.bi_annual_used
        if remaining > 0:
            allow(BI_ANNUAL, remaining)
        else:
            block(BI_ANNUAL, "already spent")

    # -- Bird family ----------------------------------------------------
    rights = player.rights
    if rights == BIRD:
        allow(BIRD, ceiling, "full Bird rights: up to the maximum")
    elif rights == EARLY_BIRD:
        allow(EARLY_BIRD, (player.prior_salary * EARLY_BIRD_RAISE_PCT) // 100,
              "175% of prior salary; the 105%-of-league-average branch is not "
              "modelled, so this is a floor on Early Bird rather than the cap")
    elif rights == NON_BIRD:
        allow(NON_BIRD, (player.prior_salary * NON_BIRD_RAISE_PCT) // 100,
              "120% of prior salary")
    else:
        block(BIRD, "not an incumbent free agent of this team")

    # -- minimum --------------------------------------------------------
    # Always available, to any team, at any cap position. This is why a
    # SigningResult is almost never empty, and why "no legal route" for a
    # normal player means something has gone wrong upstream.
    try:
        floor = minimum_salary_fn(env.season, player.years_of_service)
    except KeyError as exc:
        block(MINIMUM, f"no sourced minimum scale for {env.season}")
    else:
        if team.roster_count >= MAX_STANDARD_ROSTER:
            block(MINIMUM, f"roster is full at {team.roster_count}")
            for route in list(result.routes):
                result.routes.remove(route)
                result.blocked[route.route] = (
                    f"roster is full at {team.roster_count}"
                )
        else:
            allow(MINIMUM, floor)

    result.routes.sort(key=lambda r: (-r.max_first_year, ROUTES.index(r.route)))
    result.elapsed_s = time.monotonic() - started
    return result
