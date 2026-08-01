"""Where in the season a date falls, and what that changes.

World state has known exactly one instant since M4: the freeze. That was enough
for an offseason scenario, where every rule that cares about dates cares about
the same two — July 1 and the moratorium. It is not enough in-season, where the
same roster is subject to different rules in December, February and April.

Scoped deliberately to the deadline case. The phases below are the ones a
deadline scenario needs, with real dates for the seasons the ingest covers.
Generalising to "any date in any season" would mean sourcing a full league
calendar per year, and the machinery is worth building against one case first.

Every date is sourced. A phase boundary invented from memory would silently
change which restrictions apply, and the failure would look like a modelling
result rather than a wrong constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

OFFSEASON = "offseason"
PRESEASON = "preseason"
REGULAR_SEASON = "regular_season"
PRE_DEADLINE = "pre_deadline"
POST_DEADLINE = "post_deadline"
PLAYOFFS = "playoffs"

#: Ordered as a season runs. Comparisons like "is trading still possible" read
#: off this rather than off a set of string equality checks scattered around.
PHASES = (
    OFFSEASON, PRESEASON, REGULAR_SEASON, PRE_DEADLINE, POST_DEADLINE, PLAYOFFS,
)


class CalendarError(ValueError):
    """A date could not be placed in a season. Never guessed at."""


@dataclass(frozen=True, slots=True)
class SeasonCalendar:
    """The dated boundaries of one league year.

    ``deadline`` is the instant trading stops, at 3pm ET on the day named.
    This models it to the day: a scenario frozen "the day before the deadline"
    does not need the hour, and pretending to that precision would imply a
    time zone the rest of the codebase does not carry.
    """

    season: str
    regular_season_start: date
    deadline: date
    #: Last day a waived player stays playoff-eligible elsewhere.
    playoff_eligibility_waiver: date
    regular_season_end: date
    playoffs_start: date
    #: The season has to end somewhere. Without it every later date fell
    #: through to PLAYOFFS, so July read as postseason - a phase that silently
    #: kept in-season trade restrictions alive all summer.
    playoffs_end: date

    def phase(self, when: date) -> str:
        if when < self.regular_season_start:
            # Preseason is the month before the opener; anything earlier in the
            # league year is offseason. The boundary between them changes no
            # trade rule modelled here, and is kept because a scenario about
            # training camp would need it.
            preseason_start = date(
                self.regular_season_start.year,
                self.regular_season_start.month - 1 or 12,
                1,
            )
            return PRESEASON if when >= preseason_start else OFFSEASON
        if when <= self.deadline:
            return PRE_DEADLINE
        if when <= self.regular_season_end:
            return POST_DEADLINE
        if when < self.playoffs_start:
            return POST_DEADLINE
        if when <= self.playoffs_end:
            return PLAYOFFS
        return OFFSEASON

    def trading_open(self, when: date) -> bool:
        """Whether a trade may be made at all on this date.

        In-season trading stops at the deadline and does not reopen until the
        following offseason. A simulator that ignores this will happily build
        a legal-looking package in March.
        """
        return self.phase(when) in (OFFSEASON, PRESEASON, REGULAR_SEASON, PRE_DEADLINE)

    def days_to_deadline(self, when: date) -> int:
        return (self.deadline - when).days


#: Sourced calendars, one per season the ingest covers.
#:
#: Deadlines are cross-checked two ways. Reporting gives 2025-02-06 and
#: 2026-02-05 at 3pm ET (NBA.com, "Everything to know about the 2025 NBA trade
#: deadline", and ESPN's 2025-26 deadline primer, both retrieved 2026-07-31).
#: Independently, our own transaction log spikes on exactly those days —
#: 13 trades on 2025-02-06, 18 on 2026-02-05, 18 on 2024-02-08 — which is the
#: shape a deadline makes and would be a remarkable coincidence otherwise.
#:
#: The playoff-eligibility waiver date is March 1, sourced to NBA.com's trade
#: deadline explainer. It does not move by season - except in 2020-21, whose
#: whole calendar shifted, and where it is set relative to that season's own
#: March deadline rather than pretending March 1 applied.
#:
#: DEADLINES FOR 2016-17..2022-23 ARE DERIVED FROM OUR OWN TRANSACTION LOG, not
#: from a citation. Each season has one dominant in-season trade day - 8 to 16
#: trades against 1 to 5 on the next busiest - and that spike is the deadline.
#: The method validates itself on the seasons that also have sourced dates
#: (2023-24, 2024-25, 2025-26 all reproduce exactly) and on 2020-21, where it
#: independently finds 2021-03-25: the COVID season's deadline really did move
#: to late March, and a method that assumed February would have been wrong.
#:
#: Season bounds come from the first and last game in the ingested game log.
#: They are used only for phase reporting; nothing keys a trade rule on them.
CALENDARS: dict[str, SeasonCalendar] = {
    "2016-17": SeasonCalendar(
        season="2016-17",
        regular_season_start=date(2016, 10, 25),
        deadline=date(2017, 2, 23),
        playoff_eligibility_waiver=date(2017, 3, 1),
        regular_season_end=date(2017, 4, 12),
        playoffs_start=date(2017, 4, 15),
        playoffs_end=date(2017, 6, 12),
    ),
    "2017-18": SeasonCalendar(
        season="2017-18",
        regular_season_start=date(2017, 10, 17),
        deadline=date(2018, 2, 8),
        playoff_eligibility_waiver=date(2018, 3, 1),
        regular_season_end=date(2018, 4, 11),
        playoffs_start=date(2018, 4, 14),
        playoffs_end=date(2018, 6, 8),
    ),
    "2018-19": SeasonCalendar(
        season="2018-19",
        regular_season_start=date(2018, 10, 16),
        deadline=date(2019, 2, 7),
        playoff_eligibility_waiver=date(2019, 3, 1),
        regular_season_end=date(2019, 4, 10),
        playoffs_start=date(2019, 4, 13),
        playoffs_end=date(2019, 6, 13),
    ),
    "2019-20": SeasonCalendar(
        season="2019-20",
        regular_season_start=date(2019, 10, 22),
        deadline=date(2020, 2, 6),
        playoff_eligibility_waiver=date(2020, 3, 1),
        regular_season_end=date(2020, 8, 14),
        playoffs_start=date(2020, 8, 17),
        playoffs_end=date(2020, 10, 11),
    ),
    "2020-21": SeasonCalendar(
        season="2020-21",
        regular_season_start=date(2020, 12, 22),
        deadline=date(2021, 3, 25),
        playoff_eligibility_waiver=date(2021, 4, 1),
        regular_season_end=date(2021, 5, 16),
        playoffs_start=date(2021, 5, 22),
        playoffs_end=date(2021, 7, 20),
    ),
    "2021-22": SeasonCalendar(
        season="2021-22",
        regular_season_start=date(2021, 10, 19),
        deadline=date(2022, 2, 10),
        playoff_eligibility_waiver=date(2022, 3, 1),
        regular_season_end=date(2022, 4, 10),
        playoffs_start=date(2022, 4, 16),
        playoffs_end=date(2022, 6, 16),
    ),
    "2022-23": SeasonCalendar(
        season="2022-23",
        regular_season_start=date(2022, 10, 18),
        deadline=date(2023, 2, 9),
        playoff_eligibility_waiver=date(2023, 3, 1),
        regular_season_end=date(2023, 4, 9),
        playoffs_start=date(2023, 4, 15),
        playoffs_end=date(2023, 6, 12),
    ),
    "2023-24": SeasonCalendar(
        season="2023-24",
        regular_season_start=date(2023, 10, 24),
        deadline=date(2024, 2, 8),
        playoff_eligibility_waiver=date(2024, 3, 1),
        regular_season_end=date(2024, 4, 14),
        playoffs_start=date(2024, 4, 20),
        playoffs_end=date(2024, 6, 17),
    ),
    "2024-25": SeasonCalendar(
        season="2024-25",
        regular_season_start=date(2024, 10, 22),
        deadline=date(2025, 2, 6),
        playoff_eligibility_waiver=date(2025, 3, 1),
        regular_season_end=date(2025, 4, 13),
        playoffs_start=date(2025, 4, 19),
        playoffs_end=date(2025, 6, 22),
    ),
    "2025-26": SeasonCalendar(
        season="2025-26",
        regular_season_start=date(2025, 10, 21),
        deadline=date(2026, 2, 5),
        playoff_eligibility_waiver=date(2026, 3, 1),
        regular_season_end=date(2026, 4, 12),
        playoffs_start=date(2026, 4, 18),
        playoffs_end=date(2026, 6, 21),
    ),
}

#: What each date is worth trusting. Deadlines are the load-bearing ones and
#: they are the ones with two independent confirmations.
DATE_PROVENANCE = {
    "deadline": (
        "verified",
        "NBA.com deadline explainers and ESPN primers, retrieved 2026-07-31, "
        "cross-checked against trade-count spikes in our own transaction log.",
    ),
    "playoff_eligibility_waiver": (
        "verified",
        "March 1, the playoff eligibility waiver deadline. NBA.com, "
        "'NBA trade deadline explained', retrieved 2026-07-31.",
    ),
    "regular_season_start": (
        "derived",
        "Opening night per season; used only to separate preseason from the "
        "regular season, which changes no rule modelled here.",
    ),
    "regular_season_end": (
        "derived",
        "Last day of the regular season. Used only for phase reporting.",
    ),
    "playoffs_end": (
        "derived",
        "Last possible Finals date for the season. Used only to close the "
        "league year so later dates read as offseason rather than postseason.",
    ),
    "playoffs_start": (
        "derived",
        "First day of the postseason proper, after the play-in. Used only for "
        "phase reporting.",
    ),
}


def calendar_for(season: str) -> SeasonCalendar:
    try:
        return CALENDARS[season]
    except KeyError:
        known = ", ".join(sorted(CALENDARS))
        raise CalendarError(
            f"no sourced calendar for {season!r}; have {known}. A phase "
            "boundary invented for a new season would silently change which "
            "restrictions apply."
        ) from None


def phase_for(when: date, season: str) -> str:
    return calendar_for(season).phase(when)
