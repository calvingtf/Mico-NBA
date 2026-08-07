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
    @pytest.mark.parametrize("path", ["/", "/runs", "/results"])
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

    def test_write_without_an_id_derives_one_rather_than_refusing(self):
        """The id used to be required and the user had to invent it. It is
        derived from the resolved content now, so an absent id is normal -
        but a MALFORMED draft payload is still a 400 with the reason, not a
        500. Both halves matter: the first is the flow, the second is that a
        bad request must not read as a server crash."""
        response = client.post("/authoring/write", data={
            "draft_json": "{}", "confirmed": "yes"})
        assert response.status_code == 400
        assert "does not describe a draft" in response.text

    def test_the_confirm_gate_is_unchanged_by_the_derived_id(self):
        """Deriving the id must not have loosened the human gate."""
        response = client.post("/authoring/write", data={"draft_json": "{}"})
        assert response.status_code == 400
        assert "confirmation is a human act" in response.text

    def test_the_run_view_quotes_validator_reasons_verbatim(self):
        """Needs a run with an EVENT LOG, since the timeline is what quotes
        the validator. Chosen from disk rather than from the gallery's
        first page: the newest sixty runs are often all manifest-only, and
        a test that reads the gallery to find one was really asserting
        something about recent activity."""
        with_events = sorted(
            (d for d in (ROOT / "runs").iterdir()
             if d.is_dir() and (d / "events.jsonl").is_file()),
            key=lambda d: d.stat().st_mtime, reverse=True)
        if not with_events:
            pytest.skip("no run with an event log is present")
        page = client.get(f"/runs/{with_events[0].name}").text
        assert "refusals lead" in page.lower() or "Timeline" in page
        assert "quoted verbatim" in page

    def test_the_gallery_links_every_run_the_run_view_can_render(self):
        """The gallery linked event-log runs only, so a stipulated run a
        user had just created was the one kind they could not click through
        to - on the page whose whole job is finding runs."""
        import re

        gallery = client.get("/runs").text
        linked = set(re.findall(r'href="/runs/([^"]+)"', gallery))
        assert linked, "the gallery links nothing at all"
        for run_id in list(linked)[:5]:
            assert client.get(f"/runs/{run_id}").status_code == 200

    def test_the_limitations_cannot_be_dismissed(self):
        """They lived on /report - one link deep, showing one arbitrary
        artifact. They are on the run view now, which is the page every run
        lands on, and that is what 'structurally undismissable' has to mean
        if it means anything."""
        page = client.get("/runs/curry-lakers-2026").text
        assert "LIMITATIONS" in page
        assert "undismissable" in page
        limits = page[page.find('class="limits"'):]
        limits = limits[:limits.find("</div>")]
        assert "<details" not in limits, "limitations must not be collapsible"

    def test_every_limitation_line_survives_onto_the_page(self):
        from mironba.agents.report import LIMITATIONS

        # unescaped: Jinja turns "planner's" into "planner&#39;s", which is
        # correct output and a false negative for a literal comparison
        import html as htmlmod

        page = htmlmod.unescape(client.get("/runs/curry-lakers-2026").text)
        for item in LIMITATIONS:
            assert item in page, f"limitation dropped from the page: {item}"

    def test_manifest_fields_render_in_the_gallery(self):
        page = client.get("/runs").text
        for column in ("model", "seed", "snapshot", "gpu", "reproducible"):
            assert column in page


class TestEveryPageShowsWhatItsHeadingSays:
    """The /report failure, generalised.

    That page said "recorded output of the report agent" and showed a GM's
    chat answer, because it selected an artifact by DIRECTORY NAME and never
    checked what the completion was. The heading described the intent; the
    content came from a different filter. Same shape as a check certifying a
    different surface than its claim.

    So every remaining page states what its heading promises and what fact
    in the response proves it - and the proof has to be something only the
    right content would produce, not a word that happens to be in the
    template.
    """

    #: path -> (what the heading claims, a marker only the claimed content
    #: could put on the page)
    HEADINGS = {
        "/": ("the boundary claim and a hero from a recorded run",
              "attributable to the seed"),
        "/league": ("a league graph drawn from one recorded run",
                    'class="graphfig"'),
        "/runs": ("every run with its manifest fields",
                  "reproducible"),
        "/runs/curry-lakers-2026": ("a finished run: its graph, what the "
                                    "seed caused, and the detail report",
                                    "Detail report"),
        "/runs/curry-lakers-2026/league": ("that run's graph, full width",
                                           'class="graphfig"'),
        "/authoring": ("a box that turns a sentence into a scenario file",
                       "turns a sentence into a scenario file"),
        "/results": ("every figure with its null", "Null:"),
        "/live": ("runs to watch, and how to start one", "Run it"),
        "/demo/solver-enumeration": ("a pre-run demo of solver enumeration",
                                     "legal return packages"),
        "/demo/signing-routes": ("a pre-run demo of signing routes",
                                 "legal signing routes"),
        "/demo/validator-refusal": ("a pre-run demo of a refusal", "Refused"),
        "/branches/lebron-2026": ("two branches from one decision",
                                  "One decision, two worlds"),
    }

    @pytest.mark.parametrize("path", sorted(HEADINGS))
    def test_the_page_contains_what_its_heading_promises(self, path):
        response = client.get(path)
        if response.status_code == 404:
            pytest.skip(f"{path} has no recorded artifact to draw")
        assert response.status_code == 200, path
        _claim, marker = self.HEADINGS[path]
        # whitespace-collapsed: templates wrap their prose, so a marker can
        # span a newline and read as absent when it is plainly there. Two of
        # these failed that way first, which is a test measuring the column
        # width rather than the content.
        page = " ".join(response.text.split())
        assert " ".join(marker.split()) in page, (
            f"{path} promises {_claim!r} but the page carries no {marker!r}")

    def test_no_page_shows_a_raw_model_envelope(self):
        """The specific tell on /report: the artifact was a chat completion,
        so the page rendered `{"answer": "..."}` as prose. No page may show
        a raw schema envelope."""
        for path in sorted(self.HEADINGS):
            response = client.get(path)
            if response.status_code != 200:
                continue
            for envelope in ('{"answer"', '{"what_happened"', '{"reason"'):
                assert envelope not in response.text, (
                    f"{path} renders a raw model envelope: {envelope}")

    def test_the_retired_report_route_is_gone_not_hidden(self):
        """Retired means removed. A route left registered but unlinked is
        still a page that can be reached and still lies when it is."""
        assert client.get("/report").status_code == 404
        assert not (ROOT / "mironba" / "api" / "templates"
                    / "report.html").exists()

    def test_no_template_links_to_the_retired_route(self):
        templates = ROOT / "mironba" / "api" / "templates"
        for path in templates.glob("*.html"):
            text = path.read_text(encoding="utf-8")
            assert 'href="/report"' not in text, f"{path.name} links to /report"
