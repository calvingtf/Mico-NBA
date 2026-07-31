"""Trade rules that only bite once the season has started.

The offseason rules in ``rules/signing.py`` and ``rules/trade_validator.py``
apply year-round. These do not: they key on where a date sits in the season,
which is what ``world/calendar.py`` now answers.

Sources, all retrieved 2026-07-31:

  * NBA.com, "NBA trade deadline explained"
    https://api-hub.nba.com/news/nba-trade-deadline-explained
  * NBA.com, "Everything to know about the 2025 NBA trade deadline"
    https://www.nba.com/news/everything-to-know-about-2025-nba-trade-deadline
  * Hoops Rumors, "Key In-Season NBA Dates, Deadlines For 2025/26"
    https://www.hoopsrumors.com/2025/10/key-in-season-nba-dates-deadlines-for-2025-26.html

What is NOT modelled, named rather than left implicit because an unmodelled
restriction reads as permission:

  * 10-day contracts and the hardship exception
  * two-way contract conversions and their roster implications
  * the buyout market itself — the waiver deadline is modelled, the economics
    of a negotiated buyout are not
  * in-season tournament roster quirks
  * the disabled player exception
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from mironba.world.calendar import (
    PLAYOFFS,
    POST_DEADLINE,
    PRE_DEADLINE,
    calendar_for,
)

#: A player acquired by trade cannot have his salary aggregated with another
#: player's for this long. Distinct from the 60-day figure already in
#: constants.py, which this supersedes for the in-season case: reporting
#: describes it as two months, and two calendar months is not 60 days.
AGGREGATION_MONTHS = 2

#: A player who signs a new contract during the season generally cannot be
#: traded for three months, the same rule the offseason path already encodes.
#: Re-exported here so an in-season caller does not have to know it lives in a
#: module named for signings.
from mironba.rules.signing import signing_restriction  # noqa: E402,F401

NOT_MODELLED = (
    "10-day contracts and the hardship exception",
    "two-way contract conversions",
    "the buyout market's economics (the waiver deadline itself IS modelled)",
    "the disabled player exception",
    "in-season tournament roster rules",
)


class InSeasonRuleError(ValueError):
    """A date-dependent question could not be answered. Never defaulted."""


def add_months(start: date, months: int) -> date:
    """Calendar months, clamped to a short month. Same rule as signing.py."""
    index = start.month - 1 + months
    year = start.year + index // 12
    month = index % 12 + 1
    day = min(start.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year + month // 12, month % 12 + 1, 1) - date(year, month, 1)).days


@dataclass(frozen=True, slots=True)
class AggregationStatus:
    """Whether a recently acquired player may be packaged with another."""

    acquired_on: date
    aggregatable_from: date
    reason: str

    def aggregatable_on(self, when: date) -> bool:
        return when >= self.aggregatable_from


def aggregation_status(acquired_on: date) -> AggregationStatus:
    """When a traded-for player may be aggregated into another trade.

    Two months, not sixty days. The distinction is small and real: a player
    acquired on December 31 becomes aggregatable on February 28 or 29 under the
    calendar rule and on March 1 under the day-count rule, and a trade deadline
    routinely falls between them.
    """
    return AggregationStatus(
        acquired_on=acquired_on,
        aggregatable_from=add_months(acquired_on, AGGREGATION_MONTHS),
        reason=f"acquired by trade on {acquired_on.isoformat()}; two-month "
               "restriction on aggregating his salary",
    )


@dataclass(frozen=True, slots=True)
class PlayoffEligibility:
    """Whether a player waived on a date can play in that year's postseason."""

    waived_on: date
    deadline: date
    eligible_elsewhere: bool

    def explain(self) -> str:
        verdict = "eligible" if self.eligible_elsewhere else "INELIGIBLE"
        return (
            f"waived {self.waived_on.isoformat()} against a "
            f"{self.deadline.isoformat()} waiver deadline: {verdict} for the "
            "postseason with a new team"
        )


def playoff_eligibility(waived_on: date, season: str) -> PlayoffEligibility:
    """March 1 is the last day a waived player stays postseason-eligible.

    A player waived after it may still sign somewhere; he simply cannot play in
    the playoffs for the new team. That is a roster-construction fact a
    deadline simulation needs, because it is why buyout activity clusters in
    the last days of February.
    """
    calendar = calendar_for(season)
    return PlayoffEligibility(
        waived_on=waived_on,
        deadline=calendar.playoff_eligibility_waiver,
        eligible_elsewhere=waived_on <= calendar.playoff_eligibility_waiver,
    )


@dataclass(frozen=True, slots=True)
class TradeWindow:
    """Whether a trade may happen at all on this date, and why not."""

    when: date
    season: str
    phase: str
    open: bool
    days_to_deadline: int

    def explain(self) -> str:
        if self.open:
            return (
                f"{self.phase}: trading is open, {self.days_to_deadline} day(s) "
                "to the deadline"
            )
        return f"{self.phase}: trading closed for the season at the deadline"


def trade_window(when: date, season: str) -> TradeWindow:
    calendar = calendar_for(season)
    phase = calendar.phase(when)
    return TradeWindow(
        when=when,
        season=season,
        phase=phase,
        open=calendar.trading_open(when),
        days_to_deadline=calendar.days_to_deadline(when),
    )


def check_in_season(
    when: date,
    season: str,
    *,
    acquired_dates: dict[str, date] | None = None,
    aggregated_player_ids: tuple[str, ...] = (),
) -> list[str]:
    """Every in-season reason this trade is illegal, or an empty list.

    Returns findings rather than a boolean so a caller can report *which* rule
    bit — the same shape ``validate_trade`` uses, and for the same reason: "no"
    is not actionable and "Player X cannot be aggregated until March 3" is.
    """
    problems: list[str] = []
    window = trade_window(when, season)
    if not window.open:
        problems.append(
            f"trading is closed: {when.isoformat()} is {window.phase}, past "
            f"the {calendar_for(season).deadline.isoformat()} deadline"
        )

    for player_id in aggregated_player_ids:
        acquired = (acquired_dates or {}).get(player_id)
        if acquired is None:
            continue
        status = aggregation_status(acquired)
        if not status.aggregatable_on(when):
            problems.append(
                f"{player_id} cannot be aggregated until "
                f"{status.aggregatable_from.isoformat()} ({status.reason})"
            )
    return problems
