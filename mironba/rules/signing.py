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

from dataclasses import dataclass
from datetime import date

#: The two fixed dates the rule can land on, as (month, day).
STANDARD_UNLOCK = (12, 15)
EXTENDED_UNLOCK = (1, 15)

#: The raise that moves a Bird/Early Bird re-signing to the January date.
EXTENDED_RAISE_THRESHOLD = 1.20

#: Months of mandatory restriction from the signing date, in both cases.
RESTRICTION_MONTHS = 3


class SigningRuleError(ValueError):
    """The inputs cannot decide a date. Never defaulted to 'tradeable'."""


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
