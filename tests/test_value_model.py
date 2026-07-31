"""The v0 value model: leakage, calibration, and the delta's honesty.

The tests that matter here are not about accuracy — accuracy is measured by
``models/validate.py`` against baselines and reported in the README. These are
about the ways a validation can be quietly wrong: a fit that saw the test
season, a metric with an era trend, or a counterfactual reported as a point
estimate when it has no observable ground truth.
"""

from __future__ import annotations

import numpy as np
import pytest

from mironba.models.value import (
    EXCLUDED_SEASONS,
    FEATURES,
    MIN_MINUTES_TO_FIT,
    PlayerSeason,
    ValueModel,
    fit_value_model,
)
from mironba.models.win_delta import (
    ROOKIE_MINUTES_PER_GAME,
    WinDelta,
    WinModel,
    center_by_season,
    player_quality,
    prior_seasons,
    team_strength,
    win_delta,
)

SEASONS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24"]


def player(season, pid, minutes=1500.0, pm=0.0, games=70, **counts):
    base = {f: 0.0 for f in FEATURES}
    base.update(counts)
    return PlayerSeason(
        season=season, player_id=pid, name=pid, team="LAL",
        games=games, minutes=minutes, plus_minus=pm, counts=base,
    )


class TestNoLeakage:
    def test_prior_seasons_is_strictly_before(self):
        assert prior_seasons("2022-23", SEASONS) == ["2021-22"]
        assert "2022-23" not in prior_seasons("2022-23", SEASONS)

    def test_prior_seasons_drops_the_excluded_years(self):
        """2019-20 and 2020-21 were shortened; their wins are not comparable."""
        got = prior_seasons("2023-24", SEASONS)
        assert got == ["2021-22", "2022-23"]
        for season in EXCLUDED_SEASONS:
            assert season not in got

    def test_quality_never_uses_the_season_being_predicted(self):
        """The load-bearing anti-leakage test. A player who was terrible for
        two years and brilliant in the target season must still be priced on
        the two bad years."""
        rows = [
            player("2021-22", "p1", pm=-500.0, PTS=100),
            player("2022-23", "p1", pm=-500.0, PTS=100),
            player("2023-24", "p1", pm=+900.0, PTS=3000),
        ]
        model = fit_value_model(
            [player(f"20{y}-{y+1}", f"x{i}", pm=(i - 10) * 40, PTS=i * 90,
                    FGA=i * 60, AST=i * 20)
             for y in (21, 22) for i in range(1, 25)],
            ("2021-22", "2022-23"),
        )
        quality, _ = player_quality(model, rows, "2023-24", SEASONS)
        only_prior = player_quality(model, rows[:2], "2023-24", SEASONS)[0]
        assert quality["p1"] == pytest.approx(only_prior["p1"])

    def test_fitting_ignores_seasons_outside_the_training_set(self):
        rows = [player("2021-22", f"a{i}", pm=i * 10, PTS=i * 50, FGA=i * 40)
                for i in range(1, 30)]
        rows += [player("2023-24", f"b{i}", pm=-i * 99, PTS=i * 5, FGA=i * 400)
                 for i in range(1, 30)]
        model = fit_value_model(rows, ("2021-22",))
        assert model.n_players == 29
        assert model.fitted_on == ("2021-22",)

    def test_low_minute_seasons_do_not_set_coefficients(self):
        """A 40-minute sample has a per-minute plus/minus that is noise."""
        real = [player("2021-22", f"a{i}", minutes=2000, pm=i * 10, PTS=i * 50)
                for i in range(1, 30)]
        noise = [player("2021-22", f"n{i}", minutes=40, pm=-800, PTS=1)
                 for i in range(60)]
        assert fit_value_model(real + noise, ("2021-22",)).n_players == len(real)
        assert all(p.minutes >= MIN_MINUTES_TO_FIT for p in real)


class TestEraDrift:
    def test_centering_removes_a_league_wide_trend(self):
        """The bug this was written for: league-mean strength drifted from
        -0.72 to +0.73 across the ingested seasons with no change in how good
        the league was, because the metric weights three-pointers and
        three-point volume grew. A pooled win model reads that as improvement
        and projects the held-out season ~7.8 wins too high."""
        strengths = {}
        for i, season in enumerate(["2015-16", "2019-20", "2023-24"]):
            drift = i * 1.5
            for t in range(30):
                strengths[(season, f"t{t}")] = drift + (t - 15) * 0.1
        centered = center_by_season(strengths)
        for season in ("2015-16", "2019-20", "2023-24"):
            values = [v for (s, _), v in centered.items() if s == season]
            assert np.mean(values) == pytest.approx(0.0, abs=1e-9)

    def test_centering_preserves_within_season_ordering(self):
        """It must remove the level and nothing else."""
        strengths = {("2023-24", f"t{t}"): t * 0.3 for t in range(30)}
        centered = center_by_season(strengths)
        before = [strengths[("2023-24", f"t{t}")] for t in range(30)]
        after = [centered[("2023-24", f"t{t}")] for t in range(30)]
        assert np.argsort(before).tolist() == np.argsort(after).tolist()
        assert np.std(before) == pytest.approx(np.std(after))


class TestTeamStrength:
    def test_strength_is_a_minutes_share_weighted_mean(self):
        quality = {"a": 10.0, "b": 0.0}
        minutes = {"a": 30.0, "b": 10.0}
        strength, known, priced, filled = team_strength(["a", "b"], quality, minutes, -2.0)
        assert strength == pytest.approx(10.0 * 0.75)
        assert priced == 2 and filled == 0
        assert known == pytest.approx(1.0)

    def test_an_unknown_player_gets_replacement_not_average(self):
        """We do not know a rookie is bad. We know we cannot price him, and
        assuming league-average would make every unknown an asset."""
        strength, known, priced, filled = team_strength(
            ["known", "rookie"], {"known": 4.0}, {"known": 24.0}, -2.0
        )
        assert filled == 1 and priced == 1
        assert -2.0 < strength < 4.0
        assert known < 1.0

    def test_a_rookie_gets_a_bench_sized_share(self):
        _, known, _, _ = team_strength(["rookie"], {}, {}, -2.0)
        assert ROOKIE_MINUTES_PER_GAME > 0
        assert known == pytest.approx(0.0)

    def test_shares_absorb_the_traded_player_minute_inflation(self):
        """The stats endpoint attributes a traded player's whole season to his
        final team, so summed team minutes run ~4% high. Shares are invariant
        to that scaling; totals would not be."""
        quality = {"a": 6.0, "b": 2.0}
        normal = team_strength(["a", "b"], quality, {"a": 30.0, "b": 10.0}, -2.0)[0]
        inflated = team_strength(["a", "b"], quality, {"a": 31.2, "b": 10.4}, -2.0)[0]
        assert normal == pytest.approx(inflated)

    def test_an_empty_roster_is_replacement_level(self):
        strength, known, priced, filled = team_strength([], {}, {}, -2.0)
        assert strength == -2.0 and known == 0.0 and priced == 0 and filled == 0


class TestWinDeltaIsNeverAPoint:
    MODEL = WinModel(slope=10.0, intercept=41.0, residual_sd=8.5)

    def test_a_delta_carries_an_interval(self):
        delta = win_delta(
            ["a"], ["b"], {"a": 0.0, "b": 1.0}, {"a": 30.0, "b": 30.0},
            self.MODEL, -2.0,
        )
        low, high = delta.interval()
        assert low < delta.point < high
        assert delta.sd > 0

    def test_the_interval_is_at_least_as_wide_as_the_model_error(self):
        """A counterfactual cannot be more certain than the projections it is
        a difference of. There is no world where a team both did and did not
        make the trade, so this never becomes checkable."""
        delta = WinDelta(before=41.0, after=45.0, residual_sd=8.5)
        assert delta.point == pytest.approx(4.0)
        assert delta.sd >= 8.5

    def test_no_change_is_a_zero_delta(self):
        delta = win_delta(
            ["a"], ["a"], {"a": 3.0}, {"a": 30.0}, self.MODEL, -2.0
        )
        assert delta.point == pytest.approx(0.0)

    def test_upgrading_a_player_raises_the_projection(self):
        delta = win_delta(
            ["keep", "out"], ["keep", "in"],
            {"keep": 0.0, "out": -3.0, "in": +6.0},
            {"keep": 30.0, "out": 30.0, "in": 30.0},
            self.MODEL, -2.0,
        )
        assert delta.point > 0


class TestTheModelLayerIsDeterministic:
    def test_models_do_not_import_the_llm_layer(self):
        """Charter rule 1: an LLM may propose, only deterministic code may
        judge. A win projection that called a model would make the value of a
        trade depend on sampling temperature."""
        from pathlib import Path

        package = Path(__file__).resolve().parents[1] / "mironba" / "models"
        for path in package.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "mironba.llm" not in text, f"{path.name} imports the LLM layer"

    def test_fitting_twice_gives_the_same_model(self):
        rows = [player("2021-22", f"a{i}", pm=i * 11, PTS=i * 50, FGA=i * 40)
                for i in range(1, 40)]
        first = fit_value_model(rows, ("2021-22",))
        second = fit_value_model(rows, ("2021-22",))
        assert first.alpha == second.alpha
        assert np.allclose(first.coefficients, second.coefficients)
        assert first.replacement_pm36 == pytest.approx(second.replacement_pm36)
