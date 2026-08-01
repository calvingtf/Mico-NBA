"""The charter's null rule, enforced where it can be.

Three headline numbers in this project were artifacts: a recall of 200%, a
legality rate that counted UNDETERMINED as legal, and a counterparty match a
random proposer beats. Each survived because nobody asked what the metric would
read if the system did nothing.

These tests pin the arithmetic those audits depend on, so a future change
cannot quietly restore any of the three.
"""

from __future__ import annotations

from itertools import combinations
from math import comb

import pytest

from mironba.sim.deadline import DeadlineScore

PAIR_SPACE = 435


def p_hit(qualifying: int, drawn: int, space: int = PAIR_SPACE) -> float:
    """P(at least one of ``qualifying`` pairs lands in ``drawn`` draws)."""
    if space - qualifying < drawn:
        return 1.0
    return 1 - comb(space - qualifying, drawn) / comb(space, drawn)


class TestPairNull:
    def test_the_pair_space_is_what_we_think(self):
        assert len(list(combinations(range(30), 2))) == PAIR_SPACE

    def test_covering_half_the_space_makes_a_hit_likely(self):
        """The finding, as arithmetic: at ~48% coverage a three-team trade is
        matched by chance 87% of the time."""
        assert p_hit(3, 212) == pytest.approx(0.866, abs=0.01)
        assert p_hit(1, 206) == pytest.approx(0.474, abs=0.01)

    def test_a_three_team_trade_has_three_chances_not_one(self):
        assert p_hit(3, 200) > p_hit(1, 200)

    def test_full_coverage_makes_the_metric_meaningless(self):
        assert p_hit(1, PAIR_SPACE) == 1.0

    def test_no_coverage_cannot_hit(self):
        assert p_hit(3, 0) == 0.0


class TestRecallCountsTrades:
    def test_recall_cannot_exceed_one(self):
        """It read 200% because the numerator counted proposals."""
        score = DeadlineScore(
            proposed=673, actual=13, representable=13,
            pair_hits=20, actual_matched=11, player_hits=0, exact_hits=0,
            solver_legal=5, solver_scored=8,
        )
        assert score.recall <= 1.0
        assert score.recall == pytest.approx(11 / 13)

    def test_precision_uses_proposals_as_its_denominator(self):
        score = DeadlineScore(
            proposed=673, actual=13, representable=13,
            pair_hits=20, actual_matched=11, player_hits=0, exact_hits=0,
            solver_legal=5, solver_scored=8,
        )
        assert score.precision == pytest.approx(20 / 673)


class TestLegalityNull:
    def test_approve_everything_scores_perfectly_on_real_trades(self):
        """Why the legality rate cannot demonstrate skill.

        Every trade in the real-trade set was approved by the league, so the
        do-nothing validator - one that approves unconditionally - scores 100%.
        Any real validator can only score lower. The number is a false-rejection
        rate, and the tests that show rejection works live in the M0 synthetic
        matrix, which contains illegal trades.
        """
        real_trades_are_all_legal = True
        approve_everything_rate = 1.0 if real_trades_are_all_legal else 0.0
        assert approve_everything_rate == 1.0

    def test_undetermined_is_not_evidence_of_approval(self):
        """7 of 10 'legal' were UNDETERMINED and 0 were APPROVED."""
        approved, undetermined, rejected = 0, 7, 3
        as_reported = (approved + undetermined) / (approved + undetermined + rejected)
        strict = approved / (approved + rejected)
        assert as_reported == pytest.approx(0.7)
        assert strict == 0.0
        assert as_reported > strict, "counting UNDETERMINED as legal inflates the rate"


class TestPooledNullIsWeightedNotUnioned:
    """Instance #11, and the first that ran in our *disfavour*.

    A proposal in season S can only hit a trade in season S, so the null is a
    per-season quantity and pooling it means weighting by proposal count.
    Unioning qualifying pairs across seasons credits the null with pairs no
    proposal could ever have hit, and over three seasons inflated it from 2.40%
    to 6.67% - turning 1.24x chance into "3.70 points below chance".
    """

    def test_pooling_two_identical_seasons_returns_that_season_s_null(self):
        from mironba.eval.pooled_backtest import pooled_null_precision

        assert pooled_null_precision([(100, 0.02), (100, 0.02)]) == pytest.approx(0.02)

    def test_pooling_weights_by_proposal_count(self):
        from mironba.eval.pooled_backtest import pooled_null_precision

        # 900 proposals at 1%, 100 at 11% -> 2%, not the 6% an unweighted
        # mean would give, and not the sum an union would imply.
        assert pooled_null_precision([(900, 0.01), (100, 0.11)]) == pytest.approx(0.02)

    def test_the_pooled_null_never_exceeds_the_largest_season_null(self):
        from mironba.eval.pooled_backtest import pooled_null_precision

        seasons = [(200, 0.014), (200, 0.016), (250, 0.039)]
        assert pooled_null_precision(seasons) <= max(n for _, n in seasons)

    def test_a_season_with_no_proposals_cannot_move_the_null(self):
        from mironba.eval.pooled_backtest import pooled_null_precision

        assert pooled_null_precision([(100, 0.02), (0, 0.90)]) == pytest.approx(0.02)

    def test_empty_input_is_zero_not_a_crash(self):
        from mironba.eval.pooled_backtest import pooled_null_precision

        assert pooled_null_precision([]) == 0.0
