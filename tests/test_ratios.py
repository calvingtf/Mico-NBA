"""A ratio cannot be built from two differently-filtered sets."""

from __future__ import annotations

import pytest

from mironba.eval.ratios import Ratio, ScopeMismatch, ratio_of, refuse_prefiltered


class TestScopeCannotDiverge:
    def test_numerator_and_denominator_share_one_filter(self):
        r = ratio_of("recall", range(10), scope=lambda i: i % 2 == 0,
                     hit=lambda i: i < 5)
        assert r.denominator == 5          # 0,2,4,6,8
        assert r.numerator == 3            # 0,2,4
        assert r.value == pytest.approx(0.6)

    def test_a_ratio_above_one_is_impossible_by_construction(self):
        """The 200% and 165% recalls could not be expressed here."""
        r = ratio_of("recall", range(100), hit=lambda i: True)
        assert r.value == 1.0

    def test_a_hit_outside_the_scope_cannot_inflate_the_numerator(self):
        r = ratio_of("recall", range(10), scope=lambda i: i < 3,
                     hit=lambda i: True)
        assert r.numerator == 3 and r.denominator == 3

    def test_two_prefiltered_collections_are_refused(self):
        with pytest.raises(ScopeMismatch, match="one population and a filter"):
            refuse_prefiltered([1, 2, 3], [1, 2, 3, 4, 5])

    def test_the_refusal_says_why_equal_sizes_prove_nothing(self):
        with pytest.raises(ScopeMismatch, match="not evidence"):
            refuse_prefiltered([1], [1, 2])

    def test_an_empty_scope_is_zero_not_a_crash(self):
        r = ratio_of("recall", range(10), scope=lambda i: False)
        assert r.value == 0.0 and r.denominator == 0

    def test_the_mismatch_is_unconstructable_not_merely_guarded(self):
        """The strongest form of the property, and the reason for this class.

        ``__post_init__`` raises on numerator > denominator, but that guard can
        never fire: the numerator is drawn from the survivors of the same
        filter that produced the denominator, so it is a subset by
        construction. No combination of scope and hit can produce a ratio above
        1 - which is exactly what the 200%, the unioned null and the 165% each
        needed in order to exist.
        """
        import itertools

        population = tuple(range(8))
        predicates = [
            lambda i: True, lambda i: False,
            lambda i: i % 2 == 0, lambda i: i > 5, lambda i: i < 3,
        ]
        for scope, hit in itertools.product(predicates, repeat=2):
            r = Ratio(label="probe", population=population, scope=scope, hit=hit)
            assert r.numerator <= r.denominator
            assert 0.0 <= r.value <= 1.0


class TestReportingCarriesTheNull:
    def test_render_pairs_ratio_with_headroom(self):
        r = ratio_of("precision", range(1000), hit=lambda i: i < 36)
        line = r.render(null=0.0257)
        assert "null" in line and "x" in line and "headroom" in line

    def test_render_without_a_null_states_only_the_fraction(self):
        r = ratio_of("recall", range(10), hit=lambda i: i < 5)
        assert "null" not in r.render()
