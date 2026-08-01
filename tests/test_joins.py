"""A lookup with a fallback must be able to say whether it matched."""

from __future__ import annotations

import pytest

from mironba.data.joins import Join, JoinTooLossy


class TestHitRateIsRecorded:
    def test_a_join_that_matches_everything_reports_full_hit(self):
        join = Join("t", {"a": 1, "b": 2})
        join.get("a"); join.get("b")
        assert join.hit_rate == 1.0
        join.check()

    def test_a_join_that_matches_nothing_raises(self):
        """Instance #13: every key missed and the number looked fine."""
        join = Join("degree_weights", {"LAL": 5}, max_miss_rate=0.5)
        for key in range(30):
            join.get(key)
        assert join.hit_rate == 0.0
        with pytest.raises(JoinTooLossy, match="matched 0/30"):
            join.check()

    def test_the_error_names_the_join_and_shows_misses(self):
        join = Join("service_years", {}, max_miss_rate=0.1)
        for key in ("aaa", "bbb", "ccc"):
            join.get(key)
        with pytest.raises(JoinTooLossy) as caught:
            join.check()
        message = str(caught.value)
        assert "service_years" in message
        assert "aaa" in message

    def test_a_declared_lossy_join_does_not_raise_within_tolerance(self):
        join = Join("traded_players", {"a": 1}, max_miss_rate=0.6)
        join.get("a"); join.get("x"); join.get("y")
        assert join.hit_rate == pytest.approx(1 / 3)
        with pytest.raises(JoinTooLossy):
            join.check()
        generous = Join("traded_players", {"a": 1}, max_miss_rate=0.7)
        generous.get("a"); generous.get("x"); generous.get("y")
        generous.check()

    def test_an_unused_join_does_not_raise(self):
        Join("unused", {}).check()

    def test_the_default_is_returned_on_a_miss(self):
        join = Join("t", {}, default=7)
        assert join.get("nope") == 7

    def test_misses_are_capped_so_a_hot_loop_does_not_grow_unbounded(self):
        join = Join("t", {}, max_miss_rate=1.0)
        for key in range(500):
            join.get(key)
        assert len(join.missed_keys) <= 20
        assert join.total == 500
