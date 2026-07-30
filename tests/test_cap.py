"""Cap arithmetic against hand-computed values.

Every expected number in this file is worked out longhand in the test itself.
If a test here fails, the formula changed — not the data.
"""

from __future__ import annotations

import pytest

from mironba.rules.cap import (
    ApronTier,
    can_fit_without_aggregating,
    exception_match_limit,
    max_incoming_salary,
    pct_of,
    tier_for_salary,
)
from mironba.rules.constants import environment_for


class TestTierClassification:
    def test_bands(self, env_2526):
        env = env_2526
        assert tier_for_salary(120_000_000, env) is ApronTier.UNDER_CAP
        assert tier_for_salary(env.salary_cap, env) is ApronTier.UNDER_CAP
        assert tier_for_salary(env.salary_cap + 1, env) is ApronTier.OVER_CAP
        assert tier_for_salary(env.first_apron - 1, env) is ApronTier.OVER_CAP
        assert tier_for_salary(env.first_apron, env) is ApronTier.FIRST_APRON
        assert tier_for_salary(env.second_apron - 1, env) is ApronTier.FIRST_APRON
        assert tier_for_salary(env.second_apron, env) is ApronTier.SECOND_APRON

    def test_tiers_order_so_apron_tests_read_naturally(self):
        assert ApronTier.SECOND_APRON >= ApronTier.FIRST_APRON
        assert ApronTier.OVER_CAP < ApronTier.FIRST_APRON


class TestExceptionMatchLimit:
    """The three-bracket table, worked longhand for 2025-26 (ETPE $8,527,000)."""

    def test_small_salary_uses_the_200_percent_bracket(self, env_2526):
        # 200% x 5,000,000 + 250,000 = 10,250,000  <- median
        #        5,000,000 + 8,527,000 = 13,527,000
        # 125% x 5,000,000 + 250,000 =  6,500,000
        assert exception_match_limit(5_000_000, env_2526) == 10_250_000

    def test_mid_salary_uses_the_expanded_tpe_bracket(self, env_2526):
        # 200% x 20,000,000 + 250,000 = 40,250,000
        #        20,000,000 + 8,527,000 = 28,527,000  <- median
        # 125% x 20,000,000 + 250,000 = 25,250,000
        assert exception_match_limit(20_000_000, env_2526) == 28_527_000

    def test_large_salary_uses_the_125_percent_bracket(self, env_2526):
        # 200% x 40,000,000 + 250,000 = 80,250,000
        #        40,000,000 + 8,527,000 = 48,527,000
        # 125% x 40,000,000 + 250,000 = 50,250,000  <- median
        assert exception_match_limit(40_000_000, env_2526) == 50_250_000

    def test_upper_crossover_reproduces_the_published_boundary(self):
        """With a $7.5M expanded TPE the 125% bracket must start at $29,000,000."""
        env = environment_for("2023-24")
        assert exception_match_limit(29_000_000, env) == 29_000_000 + 7_500_000
        assert exception_match_limit(29_000_000, env) == pct_of(29_000_000, 125) + 250_000
        # One dollar past the boundary, the 125% bracket takes over.
        assert exception_match_limit(29_000_001, env) == pct_of(29_000_001, 125) + 250_000

    def test_contested_lower_boundary_at_7_4m(self):
        """Pins the $7,250,000 reading of the first bracket edge. See CONTESTED.

        At $7,400,000 outgoing in 2023-24 the two readings disagree:

          boundary at $7,250,000 (adopted): $7,400,000 is past the edge, so the
            middle bracket applies -> 7,400,000 + 7,500,000 = $14,900,000
          boundary at $7,500,000 (rejected): still in the 200% bracket ->
            2 x 7,400,000 + 250,000 = $15,050,000

        We adopt the more restrictive $14,900,000. If the other reading turns
        out to be right, this test is where it surfaces, and the error runs
        toward rejecting a legal trade rather than approving an illegal one.
        """
        env = environment_for("2023-24")
        assert exception_match_limit(7_400_000, env) == 14_900_000

    def test_published_bracket_edges_are_exact_crossovers(self):
        """The evidence the $7,250,000 reading rests on.

        The CBA Guide publishes 2026-27 bracket edges of $8,846,000 and
        $35,384,000 against an expanded TPE of $9,096,000. Both are exactly
        where adjacent formulas meet — which is only true if the brackets are
        constructed at crossovers, which is what makes the median formulation
        right and the round-number $7.5M edge wrong.
        """
        env = environment_for("2026-27")
        assert env.expanded_tpe == 9_096_000

        lower, upper = 8_846_000, 35_384_000
        # At the lower edge the 200% and middle formulas agree exactly.
        assert pct_of(lower, 200) + 250_000 == lower + env.expanded_tpe
        # At the upper edge the middle and 125% formulas agree exactly.
        assert upper + env.expanded_tpe == pct_of(upper, 125) + 250_000
        # And the median picks that shared value at both.
        assert exception_match_limit(lower, env) == lower + env.expanded_tpe
        assert exception_match_limit(upper, env) == upper + env.expanded_tpe

    def test_limit_is_monotonic_in_outgoing_salary(self, env_2526):
        """Sending out more salary can never let you take back less."""
        previous = 0
        for outgoing in range(0, 60_000_000, 250_000):
            limit = exception_match_limit(outgoing, env_2526)
            assert limit >= previous
            previous = limit


class TestMaxIncomingSalary:
    def test_under_cap_team_absorbs_into_room(self, env_2526):
        # Cap 154,647,000 - 120,000,000 = 34,647,000 of room,
        # plus 5,000,000 outgoing, plus the 250,000 cushion.
        limit = max_incoming_salary(
            5_000_000, 120_000_000, env_2526, post_trade_tier=ApronTier.UNDER_CAP
        )
        assert limit == 34_647_000 + 5_000_000 + 250_000

    def test_over_cap_team_gets_no_room_only_the_brackets(self, env_2526):
        limit = max_incoming_salary(
            20_000_000, 180_000_000, env_2526, post_trade_tier=ApronTier.OVER_CAP
        )
        assert limit == 28_527_000

    def test_apron_team_gets_flat_percentage_and_no_cushion(self, env_2526):
        limit = max_incoming_salary(
            20_000_000, 200_000_000, env_2526, post_trade_tier=ApronTier.FIRST_APRON
        )
        assert limit == 20_000_000  # 100% in 2025-26, and no +$250K

    def test_apron_limit_was_looser_in_2023_24(self, env_2324):
        limit = max_incoming_salary(
            20_000_000, 190_000_000, env_2324, post_trade_tier=ApronTier.SECOND_APRON
        )
        assert limit == 22_000_000  # 110% that season only

    def test_second_apron_is_never_more_permissive_than_below(self, env_2526):
        for outgoing in (1_000_000, 10_000_000, 30_000_000, 50_000_000):
            apron = max_incoming_salary(
                outgoing, 210_000_000, env_2526, post_trade_tier=ApronTier.SECOND_APRON
            )
            below = max_incoming_salary(
                outgoing, 180_000_000, env_2526, post_trade_tier=ApronTier.OVER_CAP
            )
            assert apron <= below


class TestAggregationPacking:
    def test_single_incoming_fits_a_single_outgoing(self, env_2526):
        assert can_fit_without_aggregating([12_000_000], [11_000_000], env_2526)

    def test_incoming_too_large_for_any_single_outgoing(self, env_2526):
        # Bins are 100% of each outgoing salary for a second-apron team.
        assert not can_fit_without_aggregating(
            [10_000_000, 12_000_000],
            [21_000_000],
            env_2526,
            per_player_limit=lambda s: s,
        )

    def test_two_incoming_split_across_two_outgoing(self, env_2526):
        assert can_fit_without_aggregating(
            [10_000_000, 12_000_000],
            [11_000_000, 9_000_000],
            env_2526,
            per_player_limit=lambda s: s,
        )

    def test_packing_needs_the_non_greedy_assignment(self, env_2526):
        """Largest-to-largest greedy alone would get this wrong.

        Bins 10 and 6; items 6 and 9. Pairing the largest item with the largest
        bin (9 -> 10) strands the 6 against a 6-capacity bin, which happens to
        work; the real trap is 9 -> 10 leaving 1 unusable. Verify the exact
        search finds the feasible assignment 9->10, 6->6.
        """
        assert can_fit_without_aggregating(
            [10_000_000, 6_000_000],
            [9_000_000, 6_000_000],
            env_2526,
            per_player_limit=lambda s: s,
        )

    def test_two_incoming_cannot_share_one_outgoing_beyond_capacity(self, env_2526):
        assert not can_fit_without_aggregating(
            [10_000_000],
            [6_000_000, 6_000_000],
            env_2526,
            per_player_limit=lambda s: s,
        )

    def test_trade_exception_supplies_an_extra_bin(self, env_2526):
        assert can_fit_without_aggregating(
            [10_000_000],
            [10_000_000, 5_000_000],
            env_2526,
            per_player_limit=lambda s: s,
            extra_bins=[5_250_000],
        )

    def test_no_incoming_always_fits(self, env_2526):
        assert can_fit_without_aggregating([5_000_000], [], env_2526)

    def test_incoming_with_no_outgoing_does_not_fit(self, env_2526):
        assert not can_fit_without_aggregating([], [5_000_000], env_2526)


def test_pct_of_uses_integer_arithmetic():
    """No floats anywhere near money: results must be exact and reproducible."""
    assert pct_of(33_333_333, 125) == 41_666_666  # floor, not 41,666,666.25
    assert isinstance(pct_of(1, 125), int)


@pytest.mark.parametrize("season", ["2023-24", "2024-25", "2025-26", "2026-27"])
def test_a_team_at_the_cap_can_always_absorb_the_cushion(season):
    env = environment_for(season)
    limit = max_incoming_salary(0, env.salary_cap, env, post_trade_tier=ApronTier.UNDER_CAP)
    assert limit == 250_000
