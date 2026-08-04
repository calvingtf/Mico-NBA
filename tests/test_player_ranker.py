"""Player-level ranker: labels, structural missingness, nulls stated."""

from __future__ import annotations

import json
from pathlib import Path

from mironba.eval.player_ranker import (
    FEATURES,
    Row,
    expiring_by_player,
    prefit_report,
    team_prior_rates,
    traded_players,
)

ROOT = Path(__file__).resolve().parents[1]


class TestLabels:
    def test_deadline_window_labels_from_the_calendar_not_a_fixed_month(self):
        """2020-21's deadline was 2021-03-25; a Jan-Feb window would miss it."""
        traded = traded_players("2020-21")
        assert len(traded) > 0

    def test_positives_are_a_minority_class(self):
        traded = traded_players("2024-25")
        assert 5 < len(traded) < 200


class TestStructuralMissingness:
    def test_expiring_is_not_computable_for_any_season(self):
        """The forward snapshot cannot testify about who was expiring: a
        contract that ended in 2025-26 had already left the page, and
        absence-from-forward-snapshot inversely encodes the label through
        post-deadline outcomes. Dropped everywhere, never proxied - this
        test originally pinned the feature to 2025-26 and is what exposed
        the leak."""
        for season in ("2019-20", "2024-25", "2025-26"):
            assert expiring_by_player(season) is None
        from mironba.eval.player_ranker import FEATURES

        assert "expiring" not in FEATURES

    def test_team_prior_rates_use_strictly_earlier_seasons(self):
        assert team_prior_rates("2016-17") == {}
        rates = team_prior_rates("2018-19")
        assert rates and all(0 <= v < 1 for v in rates.values())

    def test_prefit_reports_missingness_for_every_feature_by_class(self):
        rows = [
            Row("S", f"p{i}", "AAA", int(i < 3),
                {"availability": None if i % 2 else 0.5, "age": 25.0,
                 "log_salary": 6.0, "team_prior_rate": 0.05})
            for i in range(10)
        ]
        report = prefit_report(rows)
        assert set(report["missingness"]) == set(FEATURES)
        assert report["null_auc"] == 0.5
        assert abs(report["null_precision_at_k"] - 0.3) < 1e-9


class TestTheRecordedBench:
    def test_the_bench_file_carries_both_nulls_and_the_ablation(self):
        bench = json.loads((ROOT / "bench-player-ranker.json").read_text(
            encoding="utf-8"))
        for row in bench["per_season"]:
            assert "null_p_at_k" in row and "wt_null_p_at_k" in row
        assert bench["ablation"]["reduced"]["p_at_k"] <= \
            bench["ablation"]["full"]["p_at_k"]
        assert bench["prefit"]["positives"] > 300

    def test_the_pair_ranker_result_is_untouched(self):
        """Different unit, different question - the recorded pair negative
        stands verbatim in the README."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "6.0% against 5.01%" in readme or \
            "p@10 of 6.0% against 5.01%" in readme or \
            "6.0% against a 5.01%" in readme.replace("**", "")
