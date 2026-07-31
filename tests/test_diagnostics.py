"""The zero-sum invariant, and the paired comparison.

``TestZeroSumInvariant`` is the load-bearing one. Centring was introduced after
seeing v0 lose to its baselines, which is exactly when a "fix" deserves
suspicion. These tests assert the defect was visible *in-sample* — no held-out
season, no baseline — which is what distinguishes a correction from a rescue.
Both halves are asserted, including that the invariant fails before the fix, so
the evidence cannot quietly rot away.
"""

from __future__ import annotations

import numpy as np
import pytest

from mironba.models.diagnostics import (
    SeasonBalance,
    leave_one_season_out,
    paired_comparison,
    worst_season_error,
)


class FakeTeam:
    def __init__(self, season, team_id, wins, games=82):
        self.season, self.team_id, self.wins, self.games = season, team_id, wins, games


class FakeWinModel:
    def __init__(self, slope=10.0, intercept=41.0):
        self.slope, self.intercept = slope, intercept

    def wins(self, strength):
        return self.slope * strength + self.intercept


def league(season, drift=0.0):
    """Thirty teams sharing exactly 1230 wins, with a mean-strength offset."""
    teams, strengths = [], {}
    for i in range(30):
        wins = 20 + (i * 42) // 29  # 20..62, sums to about 1230
        teams.append(FakeTeam(season, f"t{i}", wins))
        strengths[(season, f"t{i}")] = drift + (i - 14.5) * 0.1
    # Force the total to exactly 1230 so the invariant is exact.
    total = sum(t.wins for t in teams)
    teams[0].wins += 1230 - total
    return teams, strengths


class TestZeroSumInvariant:
    def test_a_centered_model_reproduces_the_league_total(self):
        from mironba.models.diagnostics import zero_sum_balance

        teams, strengths = league("2022-23", drift=0.0)
        balances = zero_sum_balance(
            strengths, teams, FakeWinModel(), ("2022-23",)
        )
        assert len(balances) == 1
        assert balances[0].predicted_wins == pytest.approx(1230.0, abs=0.5)
        assert abs(balances[0].error_per_team) < 0.05

    def test_an_uncentered_drift_breaks_it(self):
        """The pre-fix condition, reproduced. A league-mean strength of +0.6
        with a slope of 10 puts every team 6 wins high, and no amount of
        skill at ranking teams fixes that."""
        from mironba.models.diagnostics import zero_sum_balance

        teams, strengths = league("2022-23", drift=0.6)
        balances = zero_sum_balance(
            strengths, teams, FakeWinModel(), ("2022-23",)
        )
        assert balances[0].error_per_team == pytest.approx(6.0, abs=0.1)
        assert worst_season_error(balances) > 5.0

    def test_the_drift_is_directional(self):
        """Early seasons ran low and late seasons high. Both are violations and
        a check that only caught one direction would have missed half of it."""
        from mironba.models.diagnostics import zero_sum_balance

        low_teams, low = league("2015-16", drift=-0.7)
        high_teams, high = league("2022-23", drift=+0.5)
        balances = zero_sum_balance(
            {**low, **high}, low_teams + high_teams, FakeWinModel(),
            ("2015-16", "2022-23"),
        )
        by_season = {b.season: b for b in balances}
        assert by_season["2015-16"].error_per_team < -5.0
        assert by_season["2022-23"].error_per_team > +4.0

    def test_pooled_balance_hides_what_per_season_balance_exposes(self):
        """Why an ordinary fit statistic misses this. Equal and opposite drifts
        cancel in the pooled total while every individual season is badly
        wrong — which is exactly what a least-squares intercept guarantees."""
        from mironba.models.diagnostics import zero_sum_balance

        low_teams, low = league("2015-16", drift=-0.5)
        high_teams, high = league("2022-23", drift=+0.5)
        balances = zero_sum_balance(
            {**low, **high}, low_teams + high_teams, FakeWinModel(),
            ("2015-16", "2022-23"),
        )
        pooled_error = sum(b.error for b in balances)
        assert abs(pooled_error) < 1.0, "pooled totals look fine"
        assert worst_season_error(balances) > 4.0, "per-season totals do not"

    @pytest.mark.slow
    def test_the_real_model_satisfies_it_and_the_pre_fix_model_does_not(self):
        """The claim as it appears in the README, against real data."""
        from mironba.models.validate import validate

        fixed = validate("2023-24", quiet=True, centered=True)
        broken = validate("2023-24", quiet=True, centered=False)
        assert fixed["zero_sum_worst_per_team"] < 0.01
        assert broken["zero_sum_worst_per_team"] > 5.0
        assert abs(fixed["scores"]["v0 roster model"]["bias"]) < 0.01
        assert broken["scores"]["v0 roster model"]["bias"] > 5.0


class TestPairedComparison:
    def test_a_clear_win_separates(self):
        model = [1.0] * 40
        baseline = [5.0] * 40
        result = paired_comparison("baseline", model, baseline)
        assert result.mean_difference == pytest.approx(-4.0)
        assert result.separated
        assert result.wins == 40 and result.losses == 0

    def test_a_tiny_edge_on_noisy_data_does_not_separate(self):
        """The real situation: v0 is 0.50 wins better than the regressed
        baseline over 120 team-seasons and that is p=0.159."""
        # Built from the differences directly so the effect size is the real
        # one. Composing two noisy vectors and hoping is how the first version
        # of this test ended up asserting p<0.05 on a -1.4 win effect it did
        # not intend to create.
        rng = np.random.default_rng(7)
        baseline = rng.normal(8.0, 6.0, 120)
        differences = rng.normal(-0.50, 3.86, 120)
        differences -= differences.mean() + 0.50  # pin the mean to -0.50
        model = baseline + differences
        result = paired_comparison("regressed", list(model), list(baseline))
        assert result.mean_difference == pytest.approx(-0.50, abs=0.01)
        assert not result.separated, "0.5 wins over 120 pairs is not separation"
        assert 0.10 < result.p_value < 0.30

    def test_pairing_requires_matched_arrays(self):
        with pytest.raises(ValueError, match="matched"):
            paired_comparison("x", [1.0, 2.0], [1.0])

    def test_the_sign_convention_favours_the_model_when_negative(self):
        result = paired_comparison("b", [2.0] * 30, [3.0] * 30)
        assert result.mean_difference < 0
        assert result.wins > result.losses


class TestLeaveOneSeasonOut:
    def test_it_finds_a_season_carrying_the_result(self):
        """The 2023-24 situation: three seasons of nothing and one big win.
        The pooled mean looks like a consistent edge until you drop it."""
        per_season = {
            "a": ([5.0] * 30, [5.0] * 30),
            "b": ([5.0] * 30, [5.0] * 30),
            "carrier": ([2.0] * 30, [8.0] * 30),
        }
        results = dict(leave_one_season_out(per_season, "baseline"))
        assert results["carrier"] == pytest.approx(0.0)
        assert results["a"] < -2.0
        assert results["b"] < -2.0

    def test_a_consistent_edge_survives_every_drop(self):
        per_season = {
            s: ([4.0] * 30, [5.0] * 30) for s in ("a", "b", "c")
        }
        for _, difference in leave_one_season_out(per_season, "baseline"):
            assert difference == pytest.approx(-1.0)


class TestSeasonBalanceReporting:
    def test_error_per_team_divides_by_the_teams_counted(self):
        balance = SeasonBalance("2022-23", 0.5, 1419.0, 1230.0, 30)
        assert balance.error == pytest.approx(189.0)
        assert balance.error_per_team == pytest.approx(6.3)

    def test_no_teams_is_not_a_division_error(self):
        assert SeasonBalance("x", 0.0, 0.0, 0.0, 0).error_per_team == 0.0
