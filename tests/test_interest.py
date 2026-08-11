"""Reported interest: typed, anchored, phase-partitioned, and never a score.

The circularity rule is the point of this file. Interest rows are evidence
about the outcome; once they seed the suitor set, suitor identification is
stipulated. So identification is retired as a scored metric, POST rows are
unreachable without the scoring token, and every row must anchor to an
already-verified item - an unanchored row is a new claim wearing a citation.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.world.scenario import load_scenario

_SC = load_scenario("lebron-2026")
DOCS, FREEZE = _SC.evidence_dir, _SC.freeze
from mironba.world.evidence import (
    PRE, POST, EvidenceError, ReportedInterest, load_ledger,
)
from mironba.world.relevance import REPORTED, STRUCTURAL, suitor_relevance


@pytest.fixture(scope="module")
def ledger():
    return load_ledger(DOCS, "lebron-2026", FREEZE)


class TestPartition:
    def test_post_interest_is_not_reachable_without_the_token(self, ledger):
        with pytest.raises(EvidenceError, match="outcome evidence"):
            ledger.ground_truth_interest(unlock="please")

    def test_reported_interest_returns_pre_rows_only(self, ledger):
        assert ledger.interest, "no interest rows loaded"
        for row in ledger.reported_interest():
            assert row.phase == PRE
            assert row.date <= FREEZE

    def test_phase_arithmetic_is_validated(self, ledger):
        bad = ReportedInterest(
            id="RI-XX", team="GSW", player_id="jamesle01",
            date=FREEZE.replace(year=FREEZE.year + 1), source="s", url="u",
            retrieved=FREEZE.replace(year=FREEZE.year + 1), phase=PRE,
            anchors="LBJ-04",
        )
        ledger.interest.append(bad)
        try:
            problems = ledger.validate()
        finally:
            ledger.interest.remove(bad)
        assert any("RI-XX" in p and "phase" in p for p in problems)

    def test_every_row_anchors_to_an_existing_item(self, ledger):
        assert not ledger.validate()
        for row in ledger.interest:
            assert row.anchors, f"{row.id} has no anchor"

    def test_an_unanchored_row_is_rejected(self, ledger):
        bad = ReportedInterest(
            id="RI-YY", team="GSW", player_id="jamesle01",
            date=date(2026, 6, 30), source="s", url="u",
            retrieved=date(2026, 7, 31), phase=PRE, anchors="NOPE-99",
        )
        ledger.interest.append(bad)
        try:
            problems = ledger.validate()
        finally:
            ledger.interest.remove(bad)
        assert any("new claim wearing a citation" in p for p in problems)


class TestCircularity:
    def test_lal_is_not_in_the_interest_set(self, ledger):
        """The substring-era suitor list said six; the typed rows say five.

        LBJ-01 is a departure fact - his Lakers tenure ended - and mention
        counting made it interest. Typed records exist to prevent exactly this.
        """
        teams = {r.team for r in ledger.reported_interest()
                 if r.player_id == "jamesle01"}
        assert teams == {"GSW", "CLE", "MIA", "MIN", "PHI"}

    def test_no_scored_output_is_derivable_from_pre_interest(self, ledger):
        """The scores the eval computes must need POST evidence.

        Everything readable from PRE interest alone - the suitor set, its
        size, its teams - is an input. interest_score's outputs all consume
        ground truth through the unlock token, so removing the token breaks
        them; a metric that survives without POST access is reading inputs.
        """
        import inspect

        from mironba.eval import interest_score

        source = inspect.getsource(interest_score)
        assert "SCORING_UNLOCK" in source
        assert "suitor_identification" not in source
        for name in interest_score.SCORED_OUTPUTS:
            assert "identif" not in name

    def test_the_docs_retire_identification(self):
        """UPDATED when the README was cut to a front page: the claim moved
        to docs/results.md with the result it retires. What must remain
        true is that the retirement is written down somewhere a reader
        meets it - not that it sits on the front page."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        prose = "\n".join(
            p.read_text(encoding="utf-8")
            for p in [root / "README.md", *sorted((root / "docs").glob("*.md"))])
        assert "retired as a scored metric" in prose


class TestRelevanceRouting:
    def test_reported_path_fires_where_rows_exist(self, ledger):
        teams, path = suitor_relevance("jamesle01", ledger, {"ZZZ"})
        assert path == REPORTED
        assert "ZZZ" not in teams

    def test_structural_fallback_fires_where_they_do_not(self, ledger):
        teams, path = suitor_relevance("nobody99", ledger, {"AAA", "BBB"})
        assert path == STRUCTURAL
        assert teams == ["AAA", "BBB"]

    def test_post_interest_cannot_leak_into_relevance(self, ledger):
        """LBJ-06 (POST) narrows to three teams; relevance must see five."""
        teams, _ = suitor_relevance("jamesle01", ledger, set())
        assert len(teams) == 5
