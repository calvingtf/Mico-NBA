"""The per-run league graph, and the thin cases it has to survive.

A newly authored scenario can produce few edges, no generated trades at all,
or a cascade that terminates at depth zero. None of those is a broken graph -
each is a real result - but all three look identical to a rendering failure
unless the figure says WHICH is the case. These tests pin the saying.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mironba.api.graph import run_graph
from mironba.api.ui import app

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
client = TestClient(app)


def _recorded(run_id: str) -> dict:
    path = RUNS / run_id / "manifest.json"
    if not path.is_file():
        pytest.skip(
            f"runs/{run_id} absent (runs/ is gitignored). Regenerate in ~6s: "
            f"python -m mironba.sim.stipulated --scenario {run_id} "
            f"--out runs/{run_id}/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _thin(**overrides) -> dict:
    """A manifest with a real reaction and a cascade shaped by the caller."""
    base = {
        "scenario": "thin-case", "data_snapshot": "2026-27",
        "unfalsifiable": True,
        "reaction": {"GSW": {"committed_start": 100, "committed_end": 100,
                             "signed": [], "lost_contests": []},
                     "LAL": {"committed_start": 100, "committed_end": 100,
                             "signed": [], "lost_contests": []}},
        "trade": {"label": "x", "legal": True, "players": [
            {"player_id": "curryst01", "from": "GSW", "to": "LAL",
             "salary": 1}], "findings": []},
        "cascade": {"seeded_trades": [], "unseeded_trades": [],
                    "attributable_to_seed": [], "displaced_by_seed": [],
                    "depth_reached": 2, "killed_by_counterparty_gate": 0,
                    "killed_by_solver": 0},
    }
    cascade = dict(base["cascade"])
    cascade.update(overrides.pop("cascade", {}))
    base["cascade"] = cascade
    base.update(overrides)
    return base


class TestItDrawsForAnyCompletedRun:
    @pytest.mark.parametrize("run_id", ["curry-lakers-2026",
                                        "giannis-knicks-2026",
                                        "lebron-warriors-2026"])
    def test_every_recorded_run_has_its_own_graph(self, run_id):
        """Not only the demos: the graph is a view of a manifest."""
        graph = run_graph(_recorded(run_id), run_id)
        assert graph, f"{run_id} produced no graph"
        assert len(graph["nodes"]) == 30
        assert graph["run_id"] == run_id

    @pytest.mark.parametrize("run_id", ["curry-lakers-2026",
                                        "lebron-warriors-2026"])
    def test_the_run_view_embeds_it_and_links_the_full_width_page(self, run_id):
        if not (RUNS / run_id / "manifest.json").is_file():
            pytest.skip("run absent")
        page = client.get(f"/runs/{run_id}").text
        assert "The league after this run" in page
        assert f"/runs/{run_id}/league" in page
        assert client.get(f"/runs/{run_id}/league").status_code == 200

    def test_a_manifest_only_run_is_refused_rather_than_drawn_empty(self):
        """No reaction is not an empty graph - it is a run that never got
        there, and drawing thirty bare nodes would imply otherwise."""
        assert run_graph({"scenario": "x"}, "x") == {}


class TestTheThinCasesSayWhichTheyAre:
    def test_a_run_with_no_generated_trades(self):
        graph = run_graph(_thin(cascade={
            "killed_by_counterparty_gate": 7, "killed_by_solver": 3}), "r")
        note = " ".join(graph["notes"])
        assert "NO GENERATED TRADES" in note
        assert "7 candidate pairs" in note and "3 by the solver" in note
        assert graph["by_kind"]["trade"] == 0

    def test_a_cascade_that_terminated_at_depth_zero(self):
        graph = run_graph(_thin(cascade={"depth_reached": 0}), "r")
        assert any("DEPTH ZERO" in n for n in graph["notes"])

    def test_a_run_with_no_contested_players(self):
        graph = run_graph(_thin(), "r")
        assert any("NO CONTESTED PLAYERS" in n for n in graph["notes"])
        assert graph["by_kind"]["contest"] == 0

    def test_a_run_with_no_edges_at_all_says_so(self):
        graph = run_graph(_thin(trade=None), "r")
        assert graph["edges"] == []
        assert any("NO EDGES AT ALL" in n for n in graph["notes"])

    def test_generated_trades_that_are_none_of_them_attributable(self):
        """The case the signing run actually produces: ten trades, none
        caused by the seed. Highlighting nothing is correct and has to be
        said, or the reader reads ten consequences."""
        trade = {"round": 1, "acquirer": "GSW", "counterparty": "LAL",
                 "received": [], "sent": [], "incoming_salary": 0,
                 "outgoing_salary": 0, "trigger": ""}
        graph = run_graph(_thin(cascade={
            "seeded_trades": [trade], "unseeded_trades": [trade],
            "attributable_to_seed": []}), "r")
        assert any("NO TRADE IS ATTRIBUTABLE" in n for n in graph["notes"])

    def test_a_healthy_run_carries_no_thin_notes(self):
        """The notes must not fire on a normal run, or they are decoration."""
        graph = run_graph(_recorded("curry-lakers-2026"), "curry-lakers-2026")
        assert graph["notes"] == [], graph["notes"]
        assert not graph["thin"]


class TestWhatTheGraphKeepsRight:
    def test_disposition_is_only_ever_one_of_the_three_recorded_values(self):
        graph = run_graph(_recorded("curry-lakers-2026"), "curry-lakers-2026")
        seen = {n["disposition"] for n in graph["nodes"]}
        assert seen <= {"seller", "buyer-side", "unclassified"}
        assert "unclassified" in seen, (
            "a run where every team is classified would mean the UI guessed")

    def test_positions_are_fixed_geography_not_a_layout(self):
        """Two calls must place a team identically, or two runs cannot be
        compared. A force layout would rearrange per load."""
        a = run_graph(_recorded("curry-lakers-2026"), "curry-lakers-2026")
        b = run_graph(_recorded("giannis-knicks-2026"), "giannis-knicks-2026")
        pos_a = {n["team"]: (n["x"], n["y"]) for n in a["nodes"]}
        pos_b = {n["team"]: (n["x"], n["y"]) for n in b["nodes"]}
        assert pos_a == pos_b

    def test_edges_carry_their_recorded_order(self):
        graph = run_graph(_recorded("curry-lakers-2026"), "curry-lakers-2026")
        seeds = [e for e in graph["edges"] if e["kind"] == "seed"]
        assert all(e["order"] == -1 for e in seeds), (
            "the seed precedes everything it caused")
        trades = [e["order"] for e in graph["edges"] if e["kind"] == "trade"]
        assert trades == sorted(trades)

    def test_the_caption_states_the_counts_and_the_attributable_share(self):
        page = client.get("/runs/curry-lakers-2026").text
        assert "generated trade" in page and "contested-player" in page
        assert "attributable to the seed" in page
        assert "Null:" in page
