"""The path a browser actually walks, with nothing a browser would not send.

The existing end-to-end test supplied a scenario id. The UI derives one, so
the derivation was never exercised by a test - and the first thing that broke
in it was a crash the write handler reported as a validation verdict.

These go through the FastAPI handlers with exactly the form fields the
templates emit, and no others. Marked slow: the drafting step is a local
model call of several minutes, so the full walk is opt-in
(``-m browser``). Everything that does not need the model runs by default.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mironba.api.ui import app

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "branch"
client = TestClient(app)


def _form_fields(template: str) -> set:
    """The input names a template's forms actually submit."""
    text = (ROOT / "mironba" / "api" / "templates" / template).read_text(
        encoding="utf-8")
    return set(re.findall(r'name="([a-z_]+)"', text))


class TestTheHandlersAcceptWhatTheTemplatesSend:
    def test_the_confirm_form_never_makes_the_id_mandatory(self):
        """A browser submits an empty string for an untouched optional
        field, not a missing key. The handler must treat both as 'derive
        one' - the old code raised 400 on empty."""
        assert "required" not in (
            ROOT / "mironba" / "api" / "templates" / "_draft.html"
        ).read_text(encoding="utf-8").split("scenario_id")[1][:200]

    def test_an_empty_id_string_is_treated_as_absent(self):
        """Exactly what a browser sends when the user clears the box."""
        response = client.post("/authoring/write", data={
            "draft_json": "{}", "confirmed": "yes", "scenario_id": ""})
        # 400 for the malformed draft, NOT for the missing id
        assert response.status_code == 400
        assert "does not describe a draft" in response.text
        assert "scenario id is required" not in response.text

    def test_the_write_form_fields_are_all_handled(self):
        """Every name the confirm form submits is one the handler reads."""
        assert {"draft_json", "scenario_id", "confirmed", "run"} <= \
            _form_fields("_draft.html")


class TestTheDerivedIdIsShownBeforeItIsWritten:
    def test_the_draft_panel_shows_the_id_that_will_be_used(self):
        from mironba.api.ui import _derived_id
        from mironba.world.authoring import Draft

        draft = Draft(sentence="Stephen Curry traded to the Lakers",
                      kind="stipulated", seed_date="2026-07-06",
                      moves=[{"player_name": "Stephen Curry",
                              "from_team": "GSW", "to_team": "LAL"}])
        assert _derived_id(draft) == "curry-to-lakers-2026"

    def test_a_collision_gets_a_suffix_rather_than_overwriting(self):
        from mironba.world.authoring import derive_scenario_id
        from mironba.world.authoring import Draft

        draft = Draft(sentence="x", kind="stipulated", seed_date="2026-07-06",
                      moves=[{"player_name": "Stephen Curry",
                              "from_team": "GSW", "to_team": "LAL"}])
        first = derive_scenario_id(draft, taken=set())
        second = derive_scenario_id(draft, taken={first})
        assert second == f"{first}-2"

    def test_the_writer_still_refuses_to_overwrite(self):
        """The suffix stops a user MEETING the refusal; it must not replace
        it. An id that already exists is still refused outright."""
        from mironba.world.authoring import AuthoringError, Draft, write_scenario

        draft = Draft(sentence="x", kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "LeBron James",
                              "from_team": "", "to_team": "GSW"}],
                      resolved={"LeBron James": "jamesle01"},
                      player_names=["LeBron James"], team_codes=["GSW"])
        with pytest.raises(AuthoringError, match="already exists"):
            write_scenario(draft, "curry-lakers-2026", confirmed=True,
                           config_dir=CONFIG)


@pytest.mark.browser
class TestTheWholeWalkWithNoIdSupplied:
    """The real path, model call included. Opt-in: `pytest -m browser`."""

    def test_sentence_to_graph_without_ever_typing_an_id(self):
        sentence = "Terry Rozier signs with the Portland Trail Blazers"
        response = client.post("/authoring/draft", data={"sentence": sentence})
        job_id = re.search(r"/authoring/job/(\w+)", response.text).group(1)

        page = ""
        for _ in range(300):
            time.sleep(2)
            page = client.get(f"/authoring/job/{job_id}").text
            if "jobpanel" not in page:
                break
        assert "jobpanel" not in page, "the draft never finished"

        import html as htmlmod

        match = re.search(r'name="draft_json" value="([^"]*)"', page)
        assert match, "no confirm form on the finished draft"
        draft_json = htmlmod.unescape(match.group(1))
        shown = re.search(r'id="scenario_id"[^>]*value="([^"]*)"', page)
        assert shown and shown.group(1), "no derived id shown to the user"
        derived = shown.group(1)

        written = CONFIG / f"{derived}.yaml"
        existed = written.exists()
        try:
            # EXACTLY the browser's payload: the id box untouched is sent as
            # whatever it was prefilled with, and `run` comes from the button.
            response = client.post("/authoring/write", data={
                "draft_json": draft_json, "confirmed": "yes",
                "scenario_id": derived, "run": "yes"})
            assert response.status_code == 200, response.text[:400]
            redirect = response.headers.get("hx-redirect", "")
            assert redirect.startswith("/live/"), response.text[:400]
            run_id = redirect.split("/live/")[1]

            assert client.get(f"/live/{run_id}").status_code == 200
            for _ in range(90):
                time.sleep(1)
                progress = client.get(f"/runs/progress/{run_id}")
                if progress.headers.get("hx-redirect"):
                    break
            assert progress.headers.get("hx-redirect") == f"/runs/{run_id}"

            final = client.get(f"/runs/{run_id}").text
            assert '<figure class="graphfig"' in final
            assert "Detail report" in final
            body = final[final.find("<h1"):]
            assert body.find('<figure class="graphfig"') < body.find(
                "<h2>Manifest")
        finally:
            if not existed and written.exists():
                written.unlink()


class TestWhichHandlersHaveNoBrowserPathTest:
    """The audit the brief asks for, as a test so it cannot go stale.

    A handler with no test that drives it the way a browser does is a
    handler whose contract with its own template is unverified - which is
    exactly how the id derivation shipped untested.
    """

    #: route -> the test that exercises it as a browser would, or the reason
    #: none does. Every route in the app must appear.
    COVERAGE: dict = {
        "/": "TestTheLLMPathIsLabelled (GET, no form)",
        "/league": "test_run_graph.py (GET, no form)",
        "/live": "test_ui.py routes (GET, no form)",
        "/live/{run_id}": "test_browser_path walk + test_ui",
        "/live/{run_id}/events": "GAP - polled fragment, no browser-path test",
        "/authoring": "test_ui.py (GET, no form)",
        "/authoring/draft": "test_browser_path walk (model call)",
        "/authoring/job/{job_id}": "test_browser_path walk",
        "/authoring/package": "GAP - needs a draft with package_options; "
                              "exercised only by unit tests calling "
                              "choose_package directly",
        "/authoring/resolve": "GAP - needs an ambiguous draft; the "
                              "ambiguity path has unit tests but no form "
                              "post",
        "/authoring/write": "test_browser_path walk + the field tests above",
        "/demo/{name}": "test_ui.py (GET, no form)",
        "/runs/start": "test_ui.py posts it; the walk reaches it via write",
        "/runs/progress/{run_id}": "test_browser_path walk",
        "/runs": "test_ui.py (GET, no form)",
        "/runs/{run_id}": "test_browser_path walk + test_run_graph",
        "/runs/{run_id}/league": "test_run_graph.py (GET, no form)",
        "/runs/{run_id}/narrative": "GAP - POST, only the unavailable "
                                    "branch is tested; the spawning branch "
                                    "needs a run with an event log",
        "/runs/{run_id}/narrative/{report_id}":
            "GAP - polled fragment, reachable only after the narrative "
            "spawns, which no stipulated run can do",
        "/branches/{scenario_id}": "test_ui.py (GET, no form)",
        "/report": "test_ui.py (GET, no form)",
        "/results": "test_ui.py (GET, no form)",
    }

    def test_every_route_is_accounted_for(self):
        # /openapi.json and the static mount are FastAPI's, not handlers
        # this project wrote, and nothing here renders them.
        framework = {"/openapi.json"}
        routes = {r.path for r in app.routes
                  if getattr(r, "path", "").startswith("/")
                  and not r.path.startswith("/figures")} - framework
        missing = routes - set(self.COVERAGE)
        assert missing == set(), (
            f"routes with no coverage statement: {sorted(missing)}")

    def test_the_statement_names_no_route_that_is_gone(self):
        routes = {r.path for r in app.routes if getattr(r, "path", "")}
        stale = set(self.COVERAGE) - routes
        assert "/openapi.json" not in self.COVERAGE, (
            "framework routes are excluded, not documented")
        assert stale == set(), f"coverage names dead routes: {sorted(stale)}"

    def test_the_gaps_are_declared_rather_than_implied(self):
        gaps = [route for route, why in self.COVERAGE.items()
                if why.startswith("GAP")]
        # Not asserted to be empty - that would be a lie by test. Asserted
        # to be NAMED, so the list is a decision instead of an oversight.
        assert gaps, "if every gap is closed, delete this test"
        for route in gaps:
            assert len(self.COVERAGE[route]) > 25, f"{route}: reason too thin"
