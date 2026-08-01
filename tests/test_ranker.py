"""Ranker plumbing: features, split discipline, and the permutation null.

No metric is asserted here. At the time of writing three of ten seasons had
finished and all three were 2017-CBA, so any number from this harness would be
fitted on one era and retracted later.

What is asserted is that the harness cannot lie: the split does not leak, the
null is a permutation rather than a binomial, and precision is computed from
proposals that hit rather than from distinct trades matched.
"""

from __future__ import annotations

import pytest

from mironba.eval.ranker import (
    AVAILABLE,
    NOT_AVAILABLE,
    Candidate,
    extract,
    permutation_null,
    precision_at_k,
    recall_at_all,
    report_line,
    split_by_season,
)


def _cand(season, a, b, real, era="2017"):
    return Candidate(season=season, era=era, team_a=a, team_b=b, is_real=real)


class TestSplitDiscipline:
    def test_a_season_never_appears_on_both_sides(self):
        pool = [_cand("A", "X", "Y", True), _cand("A", "X", "Z", False),
                _cand("B", "P", "Q", True)]
        train, test = split_by_season(pool, {"B"})
        assert {c.season for c in train}.isdisjoint({c.season for c in test})

    def test_the_test_season_is_wholly_held_out(self):
        pool = [_cand("A", "X", "Y", True), _cand("B", "P", "Q", True),
                _cand("B", "P", "R", False)]
        train, test = split_by_season(pool, {"B"})
        assert len(test) == 2 and len(train) == 1

    def test_an_empty_test_split_is_visible_not_silent(self):
        pool = [_cand("A", "X", "Y", True)]
        _, test = split_by_season(pool, {"Z"})
        assert test == []


class TestPermutationNullNotBinomial:
    def test_the_null_reflects_the_positive_rate(self):
        """With 1 real in 100, precision@10 by chance is ~0.01, not ~0.1.

        The head has 10 slots and the single positive lands in one of them
        10% of the time, so 0.1 expected *hits* over 10 slots is 1% precision.
        Confusing the two is how a null gets quoted an order of magnitude too
        high, which would make an observed 3% look like a loss instead of a 3x.
        """
        pool = [_cand("A", "X", f"T{i}", i == 0) for i in range(100)]
        mean, _ = permutation_null(pool, k=10, trials=4000)
        assert 0.005 < mean < 0.02, mean

    def test_a_perfect_ranking_is_significant(self):
        pool = [_cand("A", "X", f"T{i}", i < 5) for i in range(100)]
        _, p = permutation_null(pool, k=5, trials=2000)
        assert p < 0.01

    def test_a_ranking_no_better_than_chance_is_not_significant(self):
        pool = [_cand("A", "X", f"T{i}", i % 10 == 0) for i in range(100)]
        _, p = permutation_null(pool, k=10, trials=2000)
        assert p > 0.05

    def test_the_null_is_shuffled_labels_not_a_closed_form(self):
        """A binomial would treat proposals as independent draws. They are not:
        the enumerator proposes several packages per team pair, so hits are
        correlated and significance comes out overstated."""
        import inspect

        source = inspect.getsource(permutation_null)
        assert "shuffle" in source
        # A closed form would import one of these; the prose may mention the
        # word, so match on the call rather than on any occurrence of it.
        for closed_form in ("scipy.stats", "binom.sf", "binom_test", "binomtest"):
            assert closed_form not in source


class TestPrecisionCountsProposalsNotTrades:
    def test_precision_at_k_is_over_the_head_of_the_ranking(self):
        pool = [_cand("A", "X", "Y", True), _cand("A", "X", "Z", False),
                _cand("A", "X", "W", False), _cand("A", "X", "V", False)]
        assert precision_at_k(pool, 1) == 1.0
        assert precision_at_k(pool, 4) == 0.25

    def test_recall_is_the_enumerators_job_and_measured_separately(self):
        pool = [_cand("A", "X", "Y", True)]
        assert recall_at_all(pool, {frozenset(("X", "Y"))}) == 1.0
        assert recall_at_all(pool, {frozenset(("P", "Q"))}) == 0.0

    def test_k_larger_than_the_pool_does_not_divide_by_k(self):
        pool = [_cand("A", "X", "Y", True)]
        assert precision_at_k(pool, 10) == 1.0


class TestFeatureInventoryIsHonest:
    def test_unavailable_features_are_named_not_omitted(self):
        assert NOT_AVAILABLE, "the gaps must be listed, not silently absent"
        for gap in ("draft_pick_value", "injury / availability"):
            assert any(gap in f for f in NOT_AVAILABLE)

    def test_no_feature_is_both_available_and_not(self):
        assert not (set(AVAILABLE) & set(NOT_AVAILABLE))

    def test_extract_returns_only_declared_features(self):
        features = extract("2024-25", "AAA", "BBB",
                           payroll={"AAA": 150_000_000, "BBB": 140_000_000},
                           roster={"AAA": 15, "BBB": 14})
        assert features
        for name in features:
            assert name in AVAILABLE, f"{name} is not in the declared inventory"

    def test_extract_needs_no_network_or_model(self):
        features = extract("2024-25", "AAA", "BBB",
                           payroll={"AAA": 1, "BBB": 1}, roster={"AAA": 1, "BBB": 1})
        assert "salary_similarity" in features


class TestReportingShape:
    def test_a_figure_is_never_reported_without_its_null(self):
        line = report_line("precision@10", 0.12, 0.016, 0.001)
        assert "null" in line
        assert "x" in line          # the ratio
        assert "p=" in line

    def test_the_ratio_is_what_gets_quoted(self):
        assert "7.5x" in report_line("precision@10", 0.12, 0.016, 0.001)


class TestTheNullHasVariance:
    """Instance #12: a null computed as an expectation has none.

    It returned p<0.0001 for a 1.41x effect and nothing flagged it, because
    nobody checks whether a null distribution is actually a distribution.
    """

    def _seasons(self):
        return [{"pairs": [f"p{i}" for i in range(200)],
                 "n_qualifying": 10, "proposed": 200}]

    def test_the_null_distribution_is_not_degenerate(self):
        from mironba.eval.ranker import proposal_level_null

        result = proposal_level_null(self._seasons(), trials=400)
        assert result["sd"] > 0, (
            "the null has zero spread - it is computing an expectation rather "
            "than drawing outcomes, which is instance #12"
        )

    def test_the_null_spread_is_not_vanishingly_small(self):
        from mironba.eval.ranker import proposal_level_null

        result = proposal_level_null(self._seasons(), trials=400)
        assert result["sd"] > result["mean"] / 100, (
            f"sd {result['sd']:.6f} against mean {result['mean']:.6f} is too "
            "tight to be a real distribution"
        )

    def test_normalized_headroom_is_not_the_ratio(self):
        from mironba.eval.ranker import normalized_headroom

        # 1.41x looks big; closing 1% of the available gap does not.
        headroom = normalized_headroom(0.0364, 0.0258)
        assert 0.005 < headroom < 0.02
        assert headroom < (0.0364 / 0.0258) / 100

    def test_degree_weights_change_the_null(self):
        """Uneven weights only. Equal weights are refused as uniform-in-disguise
        by TestTheDegreePreservingNullIsActuallyWeighted."""
        from mironba.eval.ranker import proposal_level_null

        flat = proposal_level_null(self._seasons(), trials=300, seed=1)
        weighted = proposal_level_null(
            self._seasons(),
            degree_weights={"AAA": 90, "BBB": 60, "CCC": 2, "DDD": 1},
            trials=300, seed=1)
        assert flat["mean"] != weighted["mean"] or flat["sd"] != weighted["sd"]


class TestTheDegreePreservingNullIsActuallyWeighted:
    """Instance #13, caught before it reached a report.

    Both nulls returned byte-identical figures. The universe is pairs of
    integers; the weights were keyed by team abbreviation, so every lookup fell
    back to 1 and the "degree-preserving" null was the uniform null with a
    different label. Nothing at runtime said so - a silently-uniform weighting
    produces a perfectly plausible number.
    """

    def _seasons(self):
        return [{"pairs": [f"p{i}" for i in range(120)],
                 "n_qualifying": 8, "proposed": 120}]

    def test_identical_weights_are_refused_rather_than_reported(self):
        from mironba.eval.ranker import proposal_level_null

        with pytest.raises(ValueError, match="uniform null"):
            proposal_level_null(self._seasons(),
                                degree_weights={"AAA": 5, "BBB": 5, "CCC": 5},
                                trials=50)

    def test_a_single_team_is_refused(self):
        from mironba.eval.ranker import proposal_level_null

        with pytest.raises(ValueError, match="at least two teams"):
            proposal_level_null(self._seasons(), degree_weights={"AAA": 5}, trials=50)

    def test_uneven_weights_shift_the_null_away_from_uniform(self):
        from mironba.eval.ranker import proposal_level_null

        flat = proposal_level_null(self._seasons(), trials=400, seed=7)
        skewed = proposal_level_null(
            self._seasons(),
            degree_weights={"AAA": 100, "BBB": 100, "CCC": 1, "DDD": 1},
            trials=400, seed=7)
        assert abs(flat["mean"] - skewed["mean"]) > 1e-9 or \
               abs(flat["sd"] - skewed["sd"]) > 1e-9
