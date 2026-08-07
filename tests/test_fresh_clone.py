"""What a fresh clone sees, asserted rather than assumed.

`runs/` is gitignored, so a clone has none, and `RUNS.iterdir()` raised
FileNotFoundError. Four of the eight routes a first visitor can reach
returned 500 — the landing page among them — and every test passed, because
every test ran in a working tree that had runs.

So these run the routes with `runs/` pointed at a directory that does not
exist. An absent runs/ is a state, not a fault: it means nothing has been run
yet, which is what each page now says, along with the one command that
changes it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import mironba.api.graph as graph_mod
import mironba.api.ui as ui_mod
from mironba.api.ui import app

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)

#: Every route a first visitor can reach without an id in hand.
FIRST_VISIT = ("/", "/runs", "/live", "/league", "/authoring", "/results",
               "/branches/lebron-2026", "/demo/solver-enumeration",
               "/demo/signing-routes", "/demo/validator-refusal")


@pytest.fixture
def no_runs(tmp_path, monkeypatch):
    """Point the app at a runs/ that does not exist, as a clone has."""
    missing = tmp_path / "definitely-not-here"
    monkeypatch.setattr(ui_mod, "RUNS", missing)
    monkeypatch.setattr(graph_mod, "RUNS", missing)
    monkeypatch.setattr(ui_mod, "ROOT", tmp_path)
    assert not missing.exists()
    return missing


class TestNoRouteFallsOverOnAFreshClone:
    @pytest.mark.parametrize("path", FIRST_VISIT)
    def test_the_page_renders(self, path, no_runs):
        response = client.get(path)
        assert response.status_code == 200, (
            f"{path} returned {response.status_code} on a clone with no runs")

    @pytest.mark.parametrize("path", FIRST_VISIT)
    def test_the_page_is_not_blank_or_half_drawn(self, path, no_runs):
        """A page that renders its chrome and nothing else is worse than an
        error: the reader has to work out whether it is broken."""
        body = client.get(path).text
        body = body[body.find("<h1"):body.rfind("</body>")]
        text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
        assert len(text) > 120, f"{path} rendered almost nothing: {text!r}"

    def test_a_named_run_that_does_not_exist_is_still_a_404(self, no_runs):
        """Empty states are for pages with nothing to show. A run id that
        was never created is a genuine not-found and must stay one."""
        assert client.get("/runs/no-such-run-at-all").status_code == 404


class TestEveryEmptyStateSaysWhatFillsIt:
    CASES = {
        "/runs": ("No runs yet", "python -m mironba.sim.stipulated"),
        "/live": ("Nothing to watch yet", "python -m mironba.sim.stipulated"),
        "/league": ("No graph yet", "python -m mironba.sim.stipulated"),
        "/branches/lebron-2026": ("has not been produced yet",
                                  "python -m mironba.eval.backtest"),
    }

    @pytest.mark.parametrize("path", sorted(CASES))
    def test_it_names_what_is_missing_and_the_command(self, path, no_runs):
        what, command = self.CASES[path]
        page = client.get(path).text
        assert what in page, f"{path} does not say what is missing"
        assert command in page, f"{path} does not say what would fill it"

    def test_the_draft_area_is_never_an_unexplained_blank(self):
        """Not a clone case - it is the state of the authoring page every
        time it loads, before anything is typed."""
        page = client.get("/authoring").text
        assert "No draft yet" in page
        assert "click one of the three examples" in page


class TestTheReferenceRunIsCommitted:
    """One run ships so a clone sees a result rather than a description of
    one. A manifest is our own deterministic output - no model call in it -
    which is the line the archive draws: our outputs may be committed,
    scraped third-party data may not."""

    def test_the_reference_manifest_is_tracked(self):
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "runs/curry-lakers-2026/manifest.json",
             "branch-lebron-2026.json"],
            cwd=ROOT, capture_output=True, text=True).stdout.split()
        assert "runs/curry-lakers-2026/manifest.json" in tracked
        assert "branch-lebron-2026.json" in tracked

    def test_no_other_run_is_tracked(self):
        """The negation is narrow on purpose: one run, not a habit of
        committing output."""
        import subprocess

        tracked = subprocess.run(["git", "ls-files", "runs/"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split()
        assert tracked == ["runs/curry-lakers-2026/manifest.json"], tracked

    def test_the_committed_run_carries_no_model_output(self):
        """What makes it committable. If this ever carried a completion it
        would be model output in the repo under a policy that forbids it."""
        import json

        manifest = json.loads(
            (ROOT / "runs" / "curry-lakers-2026" / "manifest.json")
            .read_text(encoding="utf-8"))
        assert manifest["model"] is None
        assert "deterministic" in manifest["model_reason"]
        assert not (ROOT / "runs" / "curry-lakers-2026"
                    / "llm_calls.jsonl").exists()

    def test_the_landing_page_links_resolve(self):
        """The front door linked to two 404s on a clone."""
        page = client.get("/").text
        for href in sorted(set(re.findall(r'href="(/[a-z0-9/-]*)"', page))):
            assert client.get(href).status_code == 200, href
