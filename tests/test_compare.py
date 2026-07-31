"""Ranking options without claiming more than the model supports.

The boundary this enforces is the same shape as M1.5's. That milestone stopped
a model asserting a trade was *legal* when only ``rules/`` decides legality.
This stops one asserting a trade is *better* when only the win model decides
that — and at ~8.5 wins of residual, it usually cannot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mironba.models.compare import DEFAULT_Z, Comparison, Option, compare_options

RESIDUAL = 8.5


class TestTheThreshold:
    def test_it_is_wider_than_the_residual(self):
        """Differencing two projections from one model cannot be more precise
        than either of them."""
        c = compare_options([("a", 41.0), ("b", 44.0)], RESIDUAL)
        assert c.threshold > RESIDUAL
        assert c.threshold == pytest.approx(RESIDUAL * 2 ** 0.5)

    def test_the_real_situation_is_that_nothing_separates(self):
        """Three-win gaps against a 12-win threshold. This is not a corner
        case, it is what the M2 model actually produces."""
        c = compare_options(
            [("swap A", 44.2), ("swap B", 42.9), ("swap C", 41.4)], RESIDUAL
        )
        assert c.all_within_noise
        assert len(c.tiers()) == 1

    def test_a_large_gap_does_separate(self):
        c = compare_options([("blockbuster", 58.0), ("marginal", 41.0)], RESIDUAL)
        assert not c.all_within_noise
        assert len(c.tiers()) == 2

    def test_raising_z_makes_it_quieter(self):
        options = [("a", 58.0), ("b", 41.0)]
        assert len(compare_options(options, RESIDUAL, z=1.0).tiers()) == 2
        assert len(compare_options(options, RESIDUAL, z=2.0).tiers()) == 1

    def test_the_threshold_is_zero_with_no_options(self):
        assert Comparison().threshold == 0.0
        assert Comparison().tiers() == []
        assert Comparison().best_tier() == []


class TestRenderingHidesWhatIsNotReal:
    def test_a_single_tier_says_it_cannot_rank(self):
        text = compare_options([("a", 44.2), ("b", 42.9)], RESIDUAL).render()
        assert "cannot rank" in text
        assert "basketball grounds" in text

    def test_projections_are_never_shown(self):
        """A number invites arithmetic, and the differences are not real."""
        text = compare_options([("a", 44.2), ("b", 42.9)], RESIDUAL).render()
        assert "44.2" not in text and "42.9" not in text

    def test_options_within_a_tier_are_alphabetical_not_ranked(self):
        """No residual ordering for a model to read a preference out of."""
        text = compare_options(
            [("zebra", 44.2), ("alpha", 42.9), ("middle", 43.5)], RESIDUAL
        ).render()
        assert text.index("alpha") < text.index("middle") < text.index("zebra")

    def test_a_separated_tier_is_labelled_as_such(self):
        text = compare_options(
            [("big", 58.0), ("small", 41.0), ("tiny", 40.0)], RESIDUAL
        ).render()
        assert "tier 1" in text and "tier 2" in text
        assert "indistinguishable from each other" in text


class TestTiering:
    def test_a_chain_of_overlaps_stays_one_tier(self):
        """Deliberate. No adjacent pair is distinguishable, so nothing licenses
        splitting the chain even though its ends are far apart. Transitivity is
        not available here and pretending otherwise would invent a boundary."""
        c = compare_options(
            [(f"o{i}", 40.0 + i * 5.0) for i in range(6)], RESIDUAL
        )
        assert len(c.tiers()) == 1
        spread = 25.0
        assert spread > c.threshold

    def test_the_best_tier_can_hold_several_options(self):
        c = compare_options([("a", 50.0), ("b", 48.0), ("c", 20.0)], RESIDUAL)
        best = c.best_tier()
        assert {o.label for o in best} == {"a", "b"}

    def test_ordering_is_deterministic_on_ties(self):
        first = compare_options([("b", 41.0), ("a", 41.0)], RESIDUAL).render()
        second = compare_options([("a", 41.0), ("b", 41.0)], RESIDUAL).render()
        assert first == second


class TestItRefusesToGuess:
    def test_a_comparison_without_the_model_error_is_refused(self):
        """A ranking that does not know the model's error is a ranking that
        claims certainty it does not have."""
        with pytest.raises(ValueError, match="residual_sd is required"):
            compare_options([("a", 41.0)])

    def test_options_carry_their_own_residual(self):
        """So a comparison across two different models cannot be assembled by
        accident — the threshold takes the widest, not an average."""
        c = Comparison(options=[Option("a", 50.0, 2.0), Option("b", 45.0, 8.5)])
        assert c.threshold == pytest.approx(8.5 * 2 ** 0.5)


class TestItIsTheOnlyRankingPath:
    def test_no_agent_module_sorts_by_projected_wins(self):
        """The enforcement. An agent that sorted options by projection would
        express a preference the model cannot support, and it would look
        entirely reasonable in review."""
        agents = Path(__file__).resolve().parents[1] / "mironba" / "agents"
        for path in agents.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for banned in ("projected_wins", "win_delta", ".point"):
                assert banned not in text, (
                    f"{path.name} touches {banned!r}; ranking must go through "
                    "models/compare.py so ties are presented as ties"
                )

    def test_the_default_z_is_stated_rather_than_hidden(self):
        assert DEFAULT_Z == 1.0
