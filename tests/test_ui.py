"""The UI's honesty constraints - each one a test, not an intention."""

from __future__ import annotations

import ast
import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mironba.api.ui import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


class TestComputesNothing:
    def test_no_view_imports_sim_models_or_eval(self):
        """A UI that can reach the simulation can quietly become a second
        results pipeline. AST-checked, aliases included."""
        forbidden = ("mironba.sim", "mironba.models", "mironba.eval")
        offenders = []
        for path in (ROOT / "mironba" / "api").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if any(name == f or name.startswith(f + ".")
                           for f in forbidden):
                        offenders.append(f"{path.name}: {name}")
        assert not offenders, offenders


class TestNoValueWithoutItsNull:
    def test_every_figure_caption_names_its_null(self):
        page = client.get("/results").text
        figure_count = page.count("<figure>")
        assert figure_count >= 5
        assert page.count("Null:") == figure_count


class TestPostFreezeNeverRenders:
    def test_branch_page_contains_no_post_partition_item(self):
        """The partition test, extended to the UI: every phase=POST evidence
        id must be absent from the rendered page."""
        post_ids = []
        store = ROOT / "evidence" / "lebron-2026" / "lebron-2026-evidence.csv"
        with store.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("phase") == "POST":
                    post_ids.append(row["id"])
        assert post_ids, "the store should carry POST rows to test against"
        page = client.get("/branches/lebron-2026").text
        leaked = [i for i in post_ids if i in page]
        assert not leaked, f"POST evidence rendered: {leaked}"


class TestInterestIsAnInput:
    def test_the_input_marker_renders_wherever_interest_does(self):
        page = client.get("/branches/lebron-2026").text
        if "in on" in page:  # interest rows rendered
            assert "INPUT, not a prediction" in page


class TestTheLLMPathIsLabelled:
    @pytest.mark.parametrize("path", ["/", "/runs", "/results", "/report"])
    def test_one_model_per_tick_on_every_page(self, path):
        page = client.get(path).text
        assert "one model per tick" in page
        assert "not thirty agents" in page


class TestUnfalsifiableIsProminent:
    def test_counterfactual_branch_header_carries_the_badge(self):
        page = client.get("/branches/lebron-2026").text
        assert "UNFALSIFIABLE" in page
        header = page[page.index("UNFALSIFIABLE") - 200:
                      page.index("UNFALSIFIABLE") + 50]
        assert "<h2>" in header, "the flag must be a header, not a footnote"

    def test_stipulated_runs_are_badged_in_the_gallery(self):
        """Unconditional: the stipulated runs exist on disk, so the gallery
        MUST show them badged - the conditional form of this test let a
        name-sorted gallery hide every badge on page one."""
        page = client.get("/runs").text
        assert "curry-lakers-2026" in page or "giannis-knicks-2026" in page, (
            "stipulated runs missing from the newest-60 gallery")
        assert "UNFALSIFIABLE" in page


class TestTheConfirmGate:
    def test_write_without_confirmation_is_400_and_writes_nothing(self):
        before = sorted((ROOT / "configs" / "branch").glob("*.yaml"))
        response = client.post("/authoring/write", data={
            "draft_json": "{}", "scenario_id": "ui-gate-test"})
        assert response.status_code == 400
        assert "nothing was written" in response.text
        assert sorted((ROOT / "configs" / "branch").glob("*.yaml")) == before

    def test_write_without_an_id_is_400(self):
        response = client.post("/authoring/write", data={
            "draft_json": "{}", "confirmed": "yes"})
        assert response.status_code == 400


class TestScreensRender:
    def test_the_run_view_quotes_validator_reasons_verbatim(self):
        gallery = client.get("/runs").text
        import re

        run_ids = re.findall(r'href="/runs/([^"]+)"', gallery)
        assert run_ids, "no runs with event logs in the gallery"
        page = client.get(f"/runs/{run_ids[0]}").text
        assert "refusals lead" in page.lower() or "Timeline" in page
        assert "quoted verbatim" in page

    def test_the_report_limitations_cannot_be_dismissed(self):
        page = client.get("/report").text
        assert "LIMITATIONS" in page
        assert "undismissable" in page
        assert "<details" not in page, "limitations must not be collapsible"

    def test_manifest_fields_render_in_the_gallery(self):
        page = client.get("/runs").text
        for column in ("model", "seed", "snapshot", "gpu", "reproducible"):
            assert column in page
