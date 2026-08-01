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


# ---------------------------------------------------------------------------
# The property the join audit's conclusion rests on.
# ---------------------------------------------------------------------------

#: Every cross-source join feeding a verdict, with the field it populates.
#: Enumerated so a new join is covered by default rather than by memory.
VERDICT_JOINS = {
    "service_years": "years_of_service",
    "re_sign_status": "re_sign_status",
    "previous_salary": "previous_salary",
}


class TestJoinsFailTowardUnknown:
    """Measurements entry 27 concluded that no published figure needed
    restating *because* every join fails toward UNKNOWN rather than toward a
    permission. That was an argument. This is the mechanism.

    Dropping a key must move a verdict toward UNDETERMINED and never toward
    APPROVED. A join that failed the other way would silently convert missing
    data into legality, and on an all-legal real-trade set nothing would notice.
    """

    @staticmethod
    def _trade(**overrides):
        from datetime import date

        from mironba.rules.constants import environment_for
        from mironba.rules.trade_validator import (
            PlayerAsset, ReSignStatus, TeamTradeState, Trade,
        )

        env = environment_for("2024-25")
        over_cap = env.salary_cap + 15_000_000
        fields = {
            "re_sign_status": ReSignStatus.NOT_RE_SIGNED,
            "years_of_service": 6,
            "previous_salary": 19_000_000,
        }
        fields.update(overrides)
        return Trade(
            season="2024-25", trade_date=date(2025, 2, 1),
            teams=(TeamTradeState("AAA", over_cap, 14),
                   TeamTradeState("BBB", over_cap, 14)),
            players=(
                PlayerAsset("out01", "Out", 20_000_000, "AAA", "BBB", **fields),
                PlayerAsset("in01", "In", 21_000_000, "BBB", "AAA",
                            re_sign_status=ReSignStatus.NOT_RE_SIGNED),
            ),
        ), env

    def test_the_join_inventory_is_not_empty(self):
        assert VERDICT_JOINS, "no verdict-feeding joins enumerated"

    @pytest.mark.parametrize("field", sorted(VERDICT_JOINS.values()))
    def test_dropping_a_key_never_moves_the_verdict_toward_approved(self, field):
        from mironba.rules.trade_validator import ReSignStatus, Verdict, validate_trade

        complete, env = self._trade()
        baseline = validate_trade(complete, env).verdict

        missing_value = ReSignStatus.UNKNOWN if field == "re_sign_status" else None
        degraded, env = self._trade(**{field: missing_value})
        after = validate_trade(degraded, env).verdict

        rank = {Verdict.REJECTED: 0, Verdict.UNDETERMINED: 1, Verdict.APPROVED: 2}
        assert rank[after] <= rank[baseline], (
            f"dropping {field} moved the verdict from {baseline.name} to "
            f"{after.name} - a join failing toward a permission converts "
            "missing data into legality, and on an all-legal test set nothing "
            "would notice"
        )

    def test_dropping_re_sign_status_specifically_yields_undetermined(self):
        from mironba.rules.trade_validator import ReSignStatus, Verdict, validate_trade

        degraded, env = self._trade(re_sign_status=ReSignStatus.UNKNOWN,
                                    previous_salary=None)
        assert validate_trade(degraded, env).verdict is Verdict.UNDETERMINED
