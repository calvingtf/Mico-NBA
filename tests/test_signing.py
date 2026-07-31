"""Post-signing trade restrictions.

The worked case is Draymond Green, because every input is sourced: he declined
a $27,678,571 option on 2026-06-29, re-signed on 2026-07-28 at the same figure,
and his 2025-26 salary is in our own snapshot at $25,892,857. That makes the
120% test decidable from data rather than from the reporting, which matters —
the January branch and the December branch are a month apart and both look
plausible.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.rules.signing import (
    EXTENDED_RAISE_THRESHOLD,
    SigningRuleError,
    add_months,
    cap_year_start,
    signing_restriction,
)


class TestTheStandardRule:
    def test_a_july_signing_unlocks_on_december_15(self):
        """Three months from July 28 is October 28, which is earlier than
        December 15, so the fixed date binds."""
        result = signing_restriction(date(2026, 7, 28))
        assert result.trade_eligible_on == date(2026, 12, 15)
        assert result.three_month_date == date(2026, 10, 28)

    def test_a_late_september_signing_unlocks_after_december_15(self):
        """The boundary Hoops Rumors works through: sign after mid-September
        and three months carries you past the 15th, so the *later* rule bites
        in the other direction."""
        assert signing_restriction(date(2026, 9, 16)).trade_eligible_on == date(
            2026, 12, 16
        )

    def test_september_15_lands_exactly_on_december_15(self):
        assert signing_restriction(date(2026, 9, 15)).trade_eligible_on == date(
            2026, 12, 15
        )

    def test_an_august_signing_is_governed_by_december(self):
        assert signing_restriction(date(2026, 8, 16)).trade_eligible_on == date(
            2026, 12, 15
        )


class TestDraymondGreen:
    """The case the M4 backtest turns on."""

    SIGNED = date(2026, 7, 28)
    NEW = 27_678_571
    PRIOR = 25_892_857  # from snapshot bbref-2025-26

    def test_the_raise_is_below_the_120_percent_threshold(self):
        assert self.NEW < EXTENDED_RAISE_THRESHOLD * self.PRIOR
        assert round(self.NEW / self.PRIOR, 3) == 1.069

    def test_green_is_tradeable_from_december_15_2026(self):
        result = signing_restriction(
            self.SIGNED,
            bird_or_early_bird=True,
            over_cap_after_signing=True,
            first_year_salary=self.NEW,
            prior_season_salary=self.PRIOR,
        )
        assert result.trade_eligible_on == date(2026, 12, 15)
        assert result.rule == "standard free-agent signing"
        assert not result.tradeable_on(date(2026, 12, 14))
        assert result.tradeable_on(date(2026, 12, 15))

    def test_a_bigger_raise_would_have_pushed_him_to_january(self):
        """Shows the branch is live rather than dead code: the same signing at
        a 25% raise moves the date by a month."""
        result = signing_restriction(
            self.SIGNED,
            bird_or_early_bird=True,
            over_cap_after_signing=True,
            first_year_salary=int(self.PRIOR * 1.25),
            prior_season_salary=self.PRIOR,
        )
        assert result.trade_eligible_on == date(2027, 1, 15)


class TestTheExtendedRule:
    def test_all_three_conditions_are_required(self):
        big, prior = 30_000_000, 20_000_000
        signed = date(2026, 7, 10)
        both = signing_restriction(
            signed, bird_or_early_bird=True, over_cap_after_signing=True,
            first_year_salary=big, prior_season_salary=prior,
        )
        assert both.trade_eligible_on == date(2027, 1, 15)
        # Drop either flag and it falls back to December.
        for kwargs in ({"bird_or_early_bird": False, "over_cap_after_signing": True},
                       {"bird_or_early_bird": True, "over_cap_after_signing": False}):
            result = signing_restriction(
                signed, first_year_salary=big, prior_season_salary=prior, **kwargs
            )
            assert result.trade_eligible_on == date(2026, 12, 15)

    def test_exactly_120_percent_takes_the_december_branch(self):
        """The CBA says "in excess of 120%". An inclusive reading would move a
        contract's trade date a month early, which is the permissive direction
        and the one that approves an illegal trade."""
        result = signing_restriction(
            date(2026, 7, 10), bird_or_early_bird=True, over_cap_after_signing=True,
            first_year_salary=24_000_000, prior_season_salary=20_000_000,
        )
        assert result.trade_eligible_on == date(2026, 12, 15)

    def test_missing_salaries_raise_rather_than_default(self):
        """The fallback would be the permissive answer. rules/ may say it does
        not know; it may not improvise."""
        with pytest.raises(SigningRuleError, match="needs both"):
            signing_restriction(
                date(2026, 7, 10),
                bird_or_early_bird=True,
                over_cap_after_signing=True,
                first_year_salary=30_000_000,
            )


class TestCapYearAndMonthArithmetic:
    def test_a_january_signing_belongs_to_the_previous_cap_year(self):
        """Cap years run July to June. Getting this wrong puts the December
        unlock eleven months out, and it looks plausible either way."""
        assert cap_year_start(date(2027, 1, 20)) == 2026
        assert cap_year_start(date(2026, 7, 1)) == 2026
        assert cap_year_start(date(2026, 6, 30)) == 2025

    def test_a_january_signing_is_restricted_to_the_same_cap_years_december(self):
        """Signed January 20 2027, three months is April 20 2027, and the
        December 15 of *that cap year* is already past — so three months binds."""
        result = signing_restriction(date(2027, 1, 20))
        assert result.fixed_date == date(2026, 12, 15)
        assert result.trade_eligible_on == date(2027, 4, 20)

    def test_month_addition_clamps_to_a_short_month(self):
        assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
        assert add_months(date(2024, 11, 30), 3) == date(2025, 2, 28)
        assert add_months(date(2026, 10, 31), 3) == date(2027, 1, 31)
        assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)

    def test_month_addition_is_not_ninety_days(self):
        """Stated explicitly because 90 days is the tempting shortcut and it
        disagrees with the calendar by up to two days around February."""
        from datetime import timedelta

        # January 31 is where they part: three calendar months is April 30,
        # while 90 days runs to May 1. Picking a November start would have
        # proved nothing — there the two agree, which is exactly why an
        # arbitrary example is a bad test of a calendar rule.
        start = date(2027, 1, 31)
        assert add_months(start, 3) == date(2027, 4, 30)
        assert start + timedelta(days=90) == date(2027, 5, 1)
        assert add_months(start, 3) != start + timedelta(days=90)


class TestTheExplanationNamesTheBranch:
    def test_explain_shows_both_candidate_dates(self):
        text = signing_restriction(date(2026, 7, 28)).explain()
        assert "2026-12-15" in text and "2026-10-28" in text
        assert "standard free-agent signing" in text
