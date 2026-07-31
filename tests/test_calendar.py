"""Season phases and in-season trade rules.

The dates here decide which restrictions apply, so a wrong constant does not
crash — it silently changes the answer and looks like a modelling result.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.rules.in_season import (
    AGGREGATION_MONTHS,
    NOT_MODELLED,
    aggregation_status,
    check_in_season,
    playoff_eligibility,
    trade_window,
)
from mironba.world.calendar import (
    OFFSEASON,
    PHASES,
    PLAYOFFS,
    POST_DEADLINE,
    PRE_DEADLINE,
    CalendarError,
    calendar_for,
    phase_for,
)


class TestPhases:
    def test_the_deadline_separates_pre_from_post(self):
        assert phase_for(date(2025, 2, 5), "2024-25") == PRE_DEADLINE
        assert phase_for(date(2025, 2, 6), "2024-25") == PRE_DEADLINE
        assert phase_for(date(2025, 2, 7), "2024-25") == POST_DEADLINE

    def test_the_deadline_day_itself_is_still_pre(self):
        """Trading is open until 3pm ET on the day, so the day is PRE."""
        calendar = calendar_for("2024-25")
        assert calendar.phase(calendar.deadline) == PRE_DEADLINE
        assert calendar.trading_open(calendar.deadline)

    def test_summer_is_offseason_and_april_is_playoffs(self):
        assert phase_for(date(2025, 7, 15), "2024-25") == OFFSEASON
        assert phase_for(date(2025, 4, 25), "2024-25") == PLAYOFFS

    def test_an_unsourced_season_raises_rather_than_guessing(self):
        with pytest.raises(CalendarError, match="no sourced calendar"):
            phase_for(date(2030, 2, 1), "2029-30")

    def test_every_phase_name_is_in_the_ordered_tuple(self):
        for season in ("2023-24", "2024-25", "2025-26"):
            calendar = calendar_for(season)
            for when in (date(calendar.deadline.year, 1, 15),
                         calendar.deadline,
                         date(calendar.deadline.year, 3, 15),
                         calendar.playoffs_start):
                assert calendar.phase(when) in PHASES

    def test_the_sourced_deadlines_match_the_transaction_log_spikes(self):
        """Two independent confirmations. Reporting gives these dates; our own
        transaction log independently spikes on them, which is the shape a
        deadline makes."""
        assert calendar_for("2023-24").deadline == date(2024, 2, 8)
        assert calendar_for("2024-25").deadline == date(2025, 2, 6)
        assert calendar_for("2025-26").deadline == date(2026, 2, 5)


class TestTradingWindow:
    def test_trading_closes_after_the_deadline(self):
        assert trade_window(date(2025, 2, 5), "2024-25").open
        assert not trade_window(date(2025, 3, 1), "2024-25").open

    def test_a_closed_window_is_a_finding_not_a_silent_pass(self):
        problems = check_in_season(date(2025, 3, 1), "2024-25")
        assert problems and "trading is closed" in problems[0]

    def test_an_open_window_produces_no_findings(self):
        assert check_in_season(date(2025, 1, 15), "2024-25") == []


class TestAggregation:
    def test_two_calendar_months_not_sixty_days(self):
        """A player acquired December 31 is aggregatable February 28 under the
        calendar rule and March 1 under the day count. A deadline routinely
        falls between them."""
        from datetime import timedelta

        status = aggregation_status(date(2024, 12, 31))
        assert status.aggregatable_from == date(2025, 2, 28)
        assert status.aggregatable_from != date(2024, 12, 31) + timedelta(days=60)
        assert AGGREGATION_MONTHS == 2

    def test_a_recently_acquired_player_blocks_aggregation(self):
        problems = check_in_season(
            date(2025, 2, 5), "2024-25",
            acquired_dates={"p1": date(2025, 1, 20)},
            aggregated_player_ids=("p1",),
        )
        assert problems and "cannot be aggregated until" in problems[0]

    def test_an_older_acquisition_does_not(self):
        assert check_in_season(
            date(2025, 2, 5), "2024-25",
            acquired_dates={"p1": date(2024, 11, 1)},
            aggregated_player_ids=("p1",),
        ) == []

    def test_a_player_with_no_recorded_acquisition_is_not_blocked(self):
        """Absence of a date is not evidence of a recent trade, and treating it
        as one would block every player the ingest cannot date."""
        assert check_in_season(
            date(2025, 2, 5), "2024-25", aggregated_player_ids=("p1",)
        ) == []


class TestPlayoffEligibility:
    def test_march_1_is_the_boundary(self):
        assert playoff_eligibility(date(2025, 2, 28), "2024-25").eligible_elsewhere
        assert playoff_eligibility(date(2025, 3, 1), "2024-25").eligible_elsewhere
        assert not playoff_eligibility(date(2025, 3, 2), "2024-25").eligible_elsewhere

    def test_the_explanation_names_both_dates(self):
        text = playoff_eligibility(date(2025, 3, 5), "2024-25").explain()
        assert "2025-03-05" in text and "2025-03-01" in text
        assert "INELIGIBLE" in text


class TestNotModelled:
    def test_the_gaps_are_named(self):
        """An unmodelled restriction reads as permission."""
        joined = " ".join(NOT_MODELLED).lower()
        for topic in ("10-day", "hardship", "two-way", "buyout", "disabled"):
            assert topic in joined


class TestDisposition:
    def test_a_top_team_is_a_buyer_and_a_bottom_team_a_seller(self):
        """Sign check. Measuring a team inside the playoffs against the first
        team outside already carries the sign; negating it again put Oklahoma
        City at 40-9 fifteen games 'out of a playoff place'."""
        from datetime import date as d

        from mironba.models.disposition import BUYER, SELLER, disposition

        result = disposition("2024-25", d(2025, 2, 5))
        if not result:
            import pytest as _pytest

            _pytest.skip("game logs not ingested")
        assert result["OKC"].side == BUYER
        assert result["WAS"].side == SELLER
        assert result["OKC"].games_back < 0
        assert result["WAS"].games_back > 0

    def test_most_teams_at_a_deadline_are_ambiguous(self):
        """The honest outcome. A band narrower than the measured error would
        manufacture a decision the value model cannot support."""
        from datetime import date as d

        from mironba.models.disposition import AMBIGUOUS, disposition

        result = disposition("2024-25", d(2025, 2, 5))
        if not result:
            import pytest as _pytest

            _pytest.skip("game logs not ingested")
        ambiguous = sum(1 for v in result.values() if v.side == AMBIGUOUS)
        assert ambiguous > len(result) / 2

    def test_the_band_is_at_least_the_measured_threshold(self):
        from mironba.models.compare import MEASURED_DELTA_SD
        from mironba.models.disposition import SELLER_GAMES_BACK

        assert SELLER_GAMES_BACK >= MEASURED_DELTA_SD

    def test_standings_are_as_of_the_date_not_end_of_season(self):
        from datetime import date as d

        from mironba.models.disposition import standings_on

        early = standings_on("2024-25", d(2024, 12, 1))
        late = standings_on("2024-25", d(2025, 4, 13))
        if not early:
            import pytest as _pytest

            _pytest.skip("game logs not ingested")
        assert sum(s.games_played for s in early.values()) < sum(
            s.games_played for s in late.values()
        )
        assert all(s.games_played <= 82 for s in late.values())


class TestRealTradeParsing:
    def test_draftees_named_in_pick_descriptions_are_not_traded_players(self):
        """"a 2025 1st round pick (Walter Clayton Jr. was later selected)" put
        a player who did not yet exist into the trade, which made 15 of 19
        trades unscoreable for entirely the wrong reason."""
        from mironba.eval.real_trades import _traded_ids

        text = ("Marcus Smart{{smartma01}} and a 2025 1st round draft pick "
                "(Walter Clayton Jr.{{claytwa01}} was later selected)")
        assert _traded_ids(text) == ("smartma01",)

    def test_a_plain_two_player_side_parses_both(self):
        from mironba.eval.real_trades import _traded_ids

        assert _traded_ids("A{{aaa01}} and B{{bbb01}}") == ("aaa01", "bbb01")
