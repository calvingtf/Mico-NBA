"""Pre-2023 seasons run under a different CBA, and the difference is load-bearing.

Extending the backtest backwards is not a matter of swapping cap figures. The
second apron **did not exist** before 2023-24, and applying its restrictions —
no aggregation, no cash, frozen picks — to a 2019 trade would reject a deal the
league actually approved. The failure would surface as a validator legality
rate that drops in the earlier era, and it would look like a modelling result
rather than an anachronism.

So the era is a field, every rule that differs keys on it, and these tests
assert the 2023-only rules are *unreachable* in an earlier season rather than
merely unlikely to fire.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from mironba.rules.cap import ApronTier, exception_match_limit, pct_of, tier_for_salary
from mironba.rules.constants import (
    CBA_2017,
    CBA_2023,
    MATCH_BRACKETS,
    PUBLISHED_2017_CROSSOVERS,
    environment_for,
    era_for_season,
)
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    Rule,
    TeamTradeState,
    Trade,
    validate_trade,
)


@pytest.fixture
def env_2017():
    """A 2017-era environment built from a real one, so only the era differs.

    The season string stays 2023-24 while the era is forced to 2017. That
    combination is deliberately impossible in production - ``era_for_season``
    would never produce it - and it is the point: every cap figure, including
    the minimum-salary scale, is held identical so the *only* variable is the
    era. A hand-built 2019-20 environment would differ in a dozen ways and a
    passing test would not say which one mattered.

    It also cannot be built with a real pre-2023 season, because no minimum
    salary scale is sourced for one. ``cap.py`` raises rather than extrapolate,
    which is the correct behaviour and is reported as a NOT_MODELLED gap.
    """
    modern = environment_for("2023-24")
    return dataclasses.replace(modern, cba_era=CBA_2017)


class TestEraSelection:
    @pytest.mark.parametrize(
        "season,era",
        [("2016-17", CBA_2017), ("2019-20", CBA_2017), ("2022-23", CBA_2017),
         ("2023-24", CBA_2023), ("2024-25", CBA_2023), ("2025-26", CBA_2023)],
    )
    def test_era_is_derived_from_the_season(self, season, era):
        assert era_for_season(season) == era

    def test_an_environment_infers_its_era_rather_than_defaulting(self):
        """Omitting the era must not silently inherit the modern one."""
        modern = environment_for("2024-25")
        old = dataclasses.replace(modern, season="2018-19", cba_era="")
        assert old.cba_era == CBA_2017
        assert not old.has_second_apron


class TestSecondApronCannotFireBefore2023:
    def test_no_team_salary_reaches_the_second_apron_tier(self, env_2017):
        """The key assertion: SECOND_APRON is unreachable, at any payroll.

        Not "no test team crosses it" — no *possible* team salary classifies
        that way, including one far above where the modern line would sit.
        """
        for salary in (0, 100_000_000, env_2017.second_apron,
                       env_2017.second_apron * 3, 10**12):
            assert tier_for_salary(salary, env_2017) is not ApronTier.SECOND_APRON

    def test_the_same_salary_does_reach_it_under_the_2023_cba(self, env_2017):
        """The control. Without this, the test above could pass because the
        tier function is broken rather than because the era gate works."""
        modern = dataclasses.replace(env_2017, cba_era=CBA_2023)
        assert tier_for_salary(modern.second_apron, modern) is ApronTier.SECOND_APRON

    def test_aggregation_ban_does_not_fire_in_a_2017_season(self, env_2017):
        """A team far above the modern second apron aggregating two salaries."""
        trade = self._aggregating_trade(env_2017)
        result = validate_trade(trade, env_2017)
        assert not any(
            f.rule is Rule.AGGREGATION_SECOND_APRON for f in result.findings
        )

    def test_aggregation_ban_does_fire_under_the_2023_cba(self, env_2017):
        modern = dataclasses.replace(env_2017, cba_era=CBA_2023)
        result = validate_trade(self._aggregating_trade(modern), modern)
        assert any(f.rule is Rule.AGGREGATION_SECOND_APRON for f in result.findings)

    @staticmethod
    def _aggregating_trade(env):
        payroll = env.second_apron + 20_000_000
        return Trade(
            season=env.season,
            trade_date=date(int(env.season[:4]) + 1, 2, 1),
            teams=(
                TeamTradeState("AAA", payroll, 14),
                TeamTradeState("BBB", 100_000_000, 14),
            ),
            players=(
                PlayerAsset("p1", "p1", 20_000_000, "AAA", "BBB",
                            re_sign_status=ReSignStatus.UNKNOWN),
                PlayerAsset("p2", "p2", 18_000_000, "AAA", "BBB",
                            re_sign_status=ReSignStatus.UNKNOWN),
                PlayerAsset("p3", "p3", 39_000_000, "BBB", "AAA",
                            re_sign_status=ReSignStatus.UNKNOWN),
            ),
        )

    def test_no_2017_season_produces_any_second_apron_finding(self, env_2017):
        """Belt and braces across every rule whose name says second apron."""
        second_apron_rules = {
            rule for rule in Rule.__dict__.values()
            if isinstance(rule, str) and "SECOND_APRON" in rule
        }
        assert second_apron_rules, "no second-apron rules found to check"
        result = validate_trade(self._aggregating_trade(env_2017), env_2017)
        fired = {str(f.rule) for f in result.findings}
        assert not (fired & second_apron_rules)


class TestSalaryMatchingBrackets:
    def test_2017_brackets_reproduce_the_published_edges(self, env_2017):
        """The validation target the era work was checked against.

        Published 2017-CBA bracket edges are $6,533,333 and $19,600,000. The
        implementation stores percentages, not edges, so reproducing both is a
        real check rather than a restatement.

        The lower edge lands two dollars high because ``pct_of`` floors to whole
        dollars; that is a rounding artifact of integer-only arithmetic, not a
        rule difference, and it is bounded here rather than papered over.
        """
        lower, upper = PUBLISHED_2017_CROSSOVERS
        brackets = env_2017.match_brackets

        def winning_bracket(outgoing):
            values = [
                pct_of(outgoing, brackets["small_pct"]) + brackets["cushion"],
                outgoing + env_2017.middle_buffer,
                pct_of(outgoing, brackets["large_pct"]) + brackets["cushion"],
            ]
            return sorted(range(3), key=lambda i: values[i])[1]

        assert winning_bracket(lower - 1000) != winning_bracket(lower + 1000)
        assert winning_bracket(upper - 1000) != winning_bracket(upper + 1000)
        # Locate the lower crossover exactly and bound the rounding gap.
        found = next(
            x for x in range(lower - 100, lower + 100)
            if winning_bracket(x) != winning_bracket(x - 1)
        )
        assert abs(found - lower) <= 5

    def test_the_two_eras_disagree_about_what_may_come_back(self, env_2017):
        """If these matched, the era field would be doing nothing."""
        modern = dataclasses.replace(env_2017, cba_era=CBA_2023)
        for outgoing in (2_000_000, 10_000_000, 30_000_000):
            assert exception_match_limit(outgoing, env_2017) != exception_match_limit(
                outgoing, modern
            )

    def test_the_2017_cushion_is_the_smaller_one(self):
        assert MATCH_BRACKETS[CBA_2017]["cushion"] == 100_000
        assert MATCH_BRACKETS[CBA_2023]["cushion"] == 250_000

    def test_the_2017_middle_buffer_does_not_scale_with_the_cap(self, env_2017):
        """It was a flat $5M, where the modern one tracks the expanded TPE."""
        bigger_cap = dataclasses.replace(env_2017, expanded_tpe=99_000_000)
        assert bigger_cap.middle_buffer == 5_000_000
        modern = dataclasses.replace(env_2017, cba_era=CBA_2023)
        assert modern.middle_buffer == modern.expanded_tpe

    def test_a_small_outgoing_salary_gets_more_back_under_the_2023_cba(self, env_2017):
        """200% + $250K beats 175% + $100K, which is the direction of the change."""
        modern = dataclasses.replace(env_2017, cba_era=CBA_2023)
        assert exception_match_limit(3_000_000, modern) > exception_match_limit(
            3_000_000, env_2017
        )
