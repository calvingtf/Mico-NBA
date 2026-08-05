"""Figures obey the text's rules: recorded sources, nulls beside, no curation."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIGURES = ("three-arm.svg", "metrics-vs-nulls.svg", "correction-chain.svg",
           "deadline-per-season.svg", "persistence-power.svg")


class TestProvenance:
    def test_arm_aggregation_reproduces_the_recorded_table(self):
        from mironba.report.figures import arm_data

        data = arm_data()
        assert abs(data["blind"]["unreachable"] - 65.5) < 0.1
        assert abs(data["blind"]["satisfiable_first"] - 31.0) < 0.1
        assert data["feasible"]["unreachable"] == 0.0
        assert abs(data["feasible"]["satisfiable_first"] - 58.6) < 0.1
        assert data["unlock"]["satisfiable_first"] == 100.0

    def test_a_moved_anchor_fails_loudly_never_plots_a_default(self):
        from mironba.report.figures import _require

        with pytest.raises(SystemExit, match="anchor not found"):
            _require(r"this text exists nowhere 12345", "haystack", "test")

    def test_exactly_two_seasons_fall_below_their_null(self):
        from mironba.report.figures import season_series

        _, rows = season_series()
        below = {r["season"] for r in rows if r["recall"] < r["null"]}
        assert below == {"2020-21", "2025-26"}


class TestNoCurationToTheWins:
    def test_the_metrics_figure_includes_failures(self):
        from mironba.report.figures import metric_rows

        rows = metric_rows()
        losses = [r for r in rows if not r["beats"]]
        assert len(losses) >= 3, "the signature figure must keep its failures"
        # and the colour key is the RECORDED verdict, not raw direction:
        # the ranker sits numerically above its null yet is a recorded
        # negative, so it must not be painted as a win
        ranker = next(r for r in rows if "ranker" in r["label"])
        assert ranker["observed"] > ranker["null"] and not ranker["beats"]
        labels = " ".join(r["label"] for r in rows)
        for required in ("ranker", "legality", "spend"):
            assert required in labels, f"required failure missing: {required}"


class TestCommittedAndReferenced:
    def test_every_figure_exists_and_is_referenced_by_relative_path(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in FIGURES:
            path = ROOT / "docs" / "figures" / name
            assert path.is_file() and path.stat().st_size > 1000, name
            assert f"docs/figures/{name}" in readme, f"{name} not referenced"

    def test_the_readme_names_the_generator(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "mironba/report/figures.py" in readme


class TestTheCorrectionChain:
    def test_chain_values_come_from_the_records_and_carry_both_nulls(self):
        from mironba.report.figures import chain_data

        data = chain_data()
        assert data["values"][:3] == [12.4, 24.0, 9.6], (
            "the historical chain must match ledger entry #65 verbatim")
        assert abs(data["values"][3] - 11.2) < 0.05, (
            "the current headline must come from the bench, not the ledger")
        assert 5 < data["base_rate"] < 8
        assert 5 < data["wt_null"] < 9, "the chain figure must show its nulls"
