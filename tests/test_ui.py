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
        results pipeline. AST-checked, aliases included.

        The UI DOES start runs, via subprocess (mironba/api/runner.py), and
        that is not a hole in this fence. The fence is not a ban on the
        simulation happening - it is a ban on the simulation happening
        *here*, where a number could be computed, adjusted, or presented
        differently from what the CLI produces. A child process cannot do
        that: the UI holds none of its objects, passes it nothing but a
        scenario id from an allowlist, and reads back only the manifest it
        wrote to runs/ - the same file the CLI writes and the repo commits.
        The property this test states in the positive, the next one states
        in the negative: starting a run leaves mironba.sim absent from this
        process."""
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

    def test_starting_a_run_does_not_import_sim(self):
        """The fence's real claim, checked the only way that settles it.

        A subprocess would be an evasion if the simulation still ended up
        loaded in this process. Checking that in-process is worthless here:
        the rest of this suite imports mironba.sim, so the assertion would
        fail for reasons that have nothing to do with the UI. So it runs in
        a CLEAN interpreter that imports the UI and nothing else, starts a
        run through the real route, and reports what got loaded.
        """
        import subprocess
        import sys

        probe = (
            "import sys; "
            "from fastapi.testclient import TestClient; "
            "from mironba.api.ui import app; "
            "c = TestClient(app); "
            "r = c.post('/runs/start', "
            "data={'scenario_id': 'curry-lakers-2026'}); "
            "print(r.status_code, r.headers.get('hx-redirect', '')); "
            "print(sorted(m for m in sys.modules "
            "if m.startswith('mironba.sim')))"
        )
        out = subprocess.run(
            [sys.executable, "-c", probe], cwd=str(ROOT), text=True,
            capture_output=True, timeout=180)
        assert out.returncode == 0, out.stderr[-800:]
        status_line, modules_line = out.stdout.strip().splitlines()[:2]
        assert status_line.startswith("200 /live/"), status_line
        assert modules_line == "[]", (
            f"starting a run imported {modules_line} into the UI process - "
            "the subprocess boundary is what makes the fence meaningful")

    def test_a_run_can_only_be_started_for_a_declared_scenario(self):
        """The allowlist is the whole input surface: a scenario id that is
        not a declared file cannot reach the command line."""
        from mironba.api import runner

        with pytest.raises(ValueError):
            runner.start("../../etc/passwd")
        with pytest.raises(ValueError):
            runner.start("no-such-scenario")


class TestObligationsAreShownAsRequirements:
    def test_a_forced_signing_is_not_shown_as_a_choice(self):
        """A signing the rules required and a signing a team chose are
        different claims; the page has to say which."""
        page = client.get("/runs/curry-lakers-2026").text
        if "Forced to act" not in page:
            pytest.skip("curated run predates the obligations wiring")
        assert "not chosen" in page

    def test_a_hard_cap_states_whether_it_was_respected(self):
        page = client.get("/runs/curry-lakers-2026").text
        if "HARD_CAP" not in page:
            pytest.skip("curated run has no hard cap")
        assert "RESPECTED" in page or "EXCEEDED" in page


class TestTheDiffIsTheEvidence:
    def test_the_payoff_states_both_counts_never_only_the_seeded_one(self):
        """A raw '9 trades generated' is the failure this project exists to
        avoid. Both sides of the diff, or neither."""
        page = client.get("/runs/curry-lakers-2026").text
        if "What the seed caused" not in page:
            pytest.skip("curated run predates the payoff view")
        assert "unseeded run generated" in page

    def test_an_arbitrary_tiebreak_is_labelled_as_carrying_no_signal(self):
        page = client.get("/runs/giannis-knicks-2026").text
        if "went elsewhere" not in page:
            pytest.skip("no contested changes in this run")
        assert "ARBITRARY" in page

    def test_the_prerun_demo_says_which_half_it_demonstrates(self):
        """The demo skips the model. A page that hid that would be claiming
        the extraction step works when it never ran."""
        page = client.get("/demo/solver-enumeration").text
        assert "pre-run demo" in page
        assert "does not demonstrate" in page


class TestNoValueWithoutItsNull:
    def test_every_figure_caption_names_its_null(self):
        """Counts figure OPENINGS, not the bare '<figure>' string: the
        sparkline figures carry a class and the original counter skipped
        them, which would have let a figure ship without its null. This is
        the stricter form."""
        page = client.get("/results").text
        figure_count = page.count("<figure")
        assert figure_count >= 5
        assert page.count("Null:") == figure_count, (
            f"{figure_count} figures but {page.count('Null:')} nulls")

    @pytest.mark.parametrize("path", [
        "/runs/curry-lakers-2026",
        "/runs/curry-lakers-2026/league",
        "/league",
    ])
    def test_the_league_graph_is_a_figure_and_carries_its_null(self, path):
        """The graph moved onto the run view, so it comes under the same
        rule as every other figure. Its null is the unseeded run's own
        trade count - without it a reader takes every edge for a
        consequence of the scenario, and the diff says most are not."""
        response = client.get(path)
        if response.status_code == 404:
            pytest.skip(f"{path} has no recorded run to draw")
        page = response.text
        assert page.count("<figure") >= 1
        assert page.count("Null:") >= page.count("<figure"), (
            "a figure shipped without its null")
        assert "attributable to the seed" in page or "generated none" in page


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
