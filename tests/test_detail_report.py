"""The deterministic detail report, and the flow that reaches it.

The user could not find the run. The cause was structural: confirming a draft
wrote a file and stopped, leaving Run as a small button in a fragment swapped
in far below a long draft review. The confirm gate is about WRITING and it has
been passed by then, so writing now starts the run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mironba.api.detail import cascade_detail, contested_players, per_team
from mironba.api.ui import app

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
client = TestClient(app)
RUN = "curry-lakers-2026"


def _manifest(run_id: str = RUN) -> dict:
    path = RUNS / run_id / "manifest.json"
    if not path.is_file():
        pytest.skip(
            f"runs/{run_id} absent (runs/ is gitignored). Regenerate in ~6s: "
            f"python -m mironba.sim.stipulated --scenario {run_id} "
            f"--out runs/{run_id}/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


class TestTheReportNeedsNoModel:
    def test_the_run_page_states_that_nothing_waited_on_a_model(self):
        page = client.get(f"/runs/{RUN}").text
        assert "no model was called" in page
        assert "Detail report" in page

    def test_assembling_it_touches_no_llm_module(self):
        """It is a read of the manifest. If this ever needed a client, the
        report would stop being instant and the promise on the page would
        become false."""
        import ast

        src = (ROOT / "mironba" / "api" / "detail.py").read_text(
            encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith("mironba.llm"), name
                assert not name.startswith("mironba.agents"), name


class TestContestedPlayers:
    def test_only_genuinely_contested_players_are_listed(self):
        result = contested_players(_manifest())
        assert result["n"] > 0
        assert result["uncontested"] > 0, (
            "a run where every offer was contested would be suspicious")
        for row in result["rows"]:
            assert row["n_offers"] >= 2, "a single offer is not a contest"

    def test_the_resolver_reason_is_quoted_not_paraphrased(self):
        manifest = _manifest()
        reasons = {c["reason"] for c in manifest["contests"] if c["contested"]}
        for row in contested_players(manifest)["rows"]:
            assert row["reason"] in reasons

    def test_arbitrary_tiebreaks_are_counted_separately(self):
        """A coin-flip and a higher offer are different claims. Reporting
        them as one number asserts signal that is not there."""
        result = contested_players(_manifest())
        assert result["n_arbitrary"] + result["n_reasoned"] == result["n"]
        for row in result["rows"]:
            assert row["arbitrary"] == ("arbitrary" in row["reason"].lower())

    def test_the_page_labels_arbitrary_at_the_point_of_display(self):
        page = client.get(f"/runs/{RUN}").text
        if "Contested players" not in page:
            pytest.skip("no contested players in this run")
        assert "ARBITRARY" in page and "carries no signal" in page


class TestPerTeam:
    def test_every_team_reports_cap_before_and_after(self):
        rows = per_team(_manifest())
        assert len(rows) == 30
        for row in rows:
            assert row["end"] - row["start"] == row["delta"]

    def test_every_signing_names_the_route_that_paid_for_it(self):
        """'Signed Kleber' says nothing about whether that used cap room, an
        exception, or the minimum - and only the route explains why another
        team could not match."""
        rows = per_team(_manifest())
        for row in rows:
            assert len(row["routes"]) == len(row["signed"]), (
                f"{row['team']}: {len(row['signed'])} signings but "
                f"{len(row['routes'])} routes recorded")
            for entry in row["routes"]:
                assert entry["route"] and entry["salary"] > 0

    def test_teams_that_stood_pat_are_reported_not_omitted(self):
        rows = per_team(_manifest())
        quiet = [r for r in rows if not r["acted"]]
        page = client.get(f"/runs/{RUN}").text
        if quiet:
            assert "Stood pat" in page
            assert "which is a result and not a gap" in page


class TestCascadeDetail:
    def test_it_reports_what_the_gates_killed(self):
        detail = cascade_detail(_manifest())
        assert detail["considered"] == (
            detail["generated"] + detail["killed_by_gate"]
            + detail["killed_by_solver"])
        assert 0.0 <= detail["survival"] <= 1.0

    def test_the_page_says_the_gates_are_a_filter_not_a_failure(self):
        page = client.get(f"/runs/{RUN}").text
        assert "SHOULD die at the gates" in page


class TestTheFlowReachesTheRun:
    def test_the_confirm_form_offers_write_and_run_as_one_action(self):
        """The gate is on writing and has just been passed. Making a user
        find a second button afterwards is how a run gets lost."""
        from mironba.world.authoring import Draft

        template = (ROOT / "mironba" / "api" / "templates"
                    / "_draft.html").read_text(encoding="utf-8")
        assert 'name="run" value="yes"' in template
        assert "Write it and run the simulation" in template
        assert "class=\"primary\"" in template
        assert Draft  # the panel is rendered from one

    def test_the_confirm_form_states_what_running_produces_and_its_cost(self):
        template = (ROOT / "mironba" / "api" / "templates"
                    / "_draft.html").read_text(encoding="utf-8")
        assert "thirty teams" in template
        assert "typical_s" in template, "the duration must be measured"
        assert "no model call" in template

    def test_write_without_run_still_offers_run_as_the_primary_control(self):
        template = (ROOT / "mironba" / "api" / "templates"
                    / "_written.html").read_text(encoding="utf-8")
        assert "primary big" in template
        assert "Run the simulation" in template

    def test_the_confirm_gate_still_refuses_an_unconfirmed_write(self):
        """Write-and-run must not have loosened the human gate."""
        response = client.post("/authoring/write", data={
            "draft_json": "{}", "scenario_id": "x", "run": "yes"})
        assert response.status_code == 400


class TestTheNarrativeIsOptional:
    def test_it_is_never_on_the_path_of_the_run_output(self):
        page = client.get(f"/runs/{RUN}").text
        detail_at = page.find("Detail report")
        narrative_at = page.find("narrative")
        assert detail_at > 0 and narrative_at > detail_at, (
            "the deterministic report must already be on screen")

    def test_it_states_that_it_is_a_model_call_and_slow(self):
        page = client.get(f"/runs/{RUN}").text
        assert "language model" in page
        assert "minutes, not seconds" in page

    def test_a_manifest_only_run_can_now_be_summarised(self):
        """It could not be, and that was the point of repointing the agent:
        the one run kind the UI creates was the one kind it could not
        describe."""
        from mironba.api import runner

        ok, why = runner.report_available(RUN)
        assert ok, why
        page = client.get(f"/runs/{RUN}").text
        assert "Not available for this run" not in page
        assert "Write the narrative summary" in page

    def test_a_run_that_recorded_nothing_still_says_why(self):
        from mironba.api import runner

        ok, why = runner.report_available("no-such-run-anywhere")
        assert not ok and "no such run" in why
