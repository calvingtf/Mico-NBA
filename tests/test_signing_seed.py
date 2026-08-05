"""The second kind of seed: a stipulated SIGNING.

A trade is judged by ``rules/trade_validator.py``. A signing has no
counterparty and no salary matching, so that validator has nothing to say
about it and ``rules/signing.py`` answers the question that does apply -
does the destination have a legal route, and on what terms.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mironba.rules.constants import environment_for
from mironba.world.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "runs" / "lebron-warriors-2026"

#: runs/ is gitignored - the artifacts are regenerable and deterministic, so
#: the repo does not carry them. A skip that does not say how to un-skip
#: itself is a test that quietly stops running.
REGENERATE = (
    "run artifact absent (runs/ is gitignored). Regenerate in ~6s: "
    "python -m mironba.sim.stipulated --scenario lebron-warriors-2026 "
    "--out runs/lebron-warriors-2026/manifest.json"
)


@pytest.fixture(scope="module")
def bound():
    import mironba.sim.league as league_mod

    scenario = load_scenario("lebron-warriors-2026")
    league_mod.bind_scenario(scenario)
    league_mod.TEAMS = league_mod._all_teams()
    return scenario, league_mod, league_mod.LeagueState.load()


class TestTheAmountIsNeverStated:
    def test_the_scenario_file_declares_no_salary(self):
        """Same rule as a stipulated trade: the file says WHO and WHERE.
        A salary in the yaml would be a number nobody checked."""
        text = (ROOT / "configs" / "branch"
                / "lebron-warriors-2026.yaml").read_text(encoding="utf-8")
        spec = load_scenario("lebron-warriors-2026").stipulation["signing"]
        assert "salary" not in spec
        assert "salary" not in text.split("signing:")[1]

    def test_the_run_records_that_the_figure_was_derived(self):
        if not (RUN / "manifest.json").is_file():
            pytest.skip(REGENERATE)
        signing = json.loads(
            (RUN / "manifest.json").read_text(encoding="utf-8"))["signing"]
        assert signing["salary"] > 0
        assert "derived" in signing["salary_source"]
        assert signing["route"] in {r["route"] for r in signing["routes"]}


class TestTheSigningSolverDecides:
    def test_a_legal_signing_reports_every_route_and_its_maximum(self, bound):
        from mironba.sim.signing_seed import build_signing, routes_for

        scenario, league_mod, league = bound
        signing = build_signing(scenario, league, league_mod)
        result = routes_for(signing, league, environment_for("2026-27"))
        assert result.routes
        for route in result.routes:
            assert route.max_first_year > 0
            assert route.describe()

    def test_no_route_quotes_the_binding_constraint(self, bound):
        """A refusal is a real answer about the counterfactual, and it has
        to say WHY - the same standard the trade path already meets."""
        from dataclasses import replace

        from mironba.sim.signing_seed import routes_for

        scenario, league_mod, league = bound
        from mironba.sim.signing_seed import build_signing

        signing = build_signing(scenario, league, league_mod)
        # LAL carries 16 players at the freeze: the roster, not the money,
        # is what blocks it, and the refusal should say so.
        blocked = routes_for(replace(signing, to_team="LAL"), league,
                             environment_for("2026-27"))
        assert not blocked.routes
        assert blocked.blocked, "a refusal with no reason is not an answer"
        assert any("roster" in why for why in blocked.blocked.values())


class TestTheSnapshotOverrulesTheSentence:
    def test_a_player_under_contract_cannot_be_signed(self, bound):
        """Whether a player is a free agent is a fact, not a reading of the
        sentence. Curry is on Golden State in both season snapshots."""
        from mironba.world.authoring import Draft, validate_draft

        draft = Draft(sentence="Stephen Curry signs with the Warriors",
                      kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "Stephen Curry",
                              "from_team": "", "to_team": "Warriors"}])
        validate_draft(draft)
        assert draft.errors
        assert "not a free agent" in " ".join(draft.errors)
        assert any("traded" in step for step in draft.next_steps)

    def test_a_post_freeze_arrival_is_signable_at_the_freeze(self, bound):
        """The contract file is an end-of-season artifact. Refusing on the
        row alone would use post-freeze information to rule out a
        counterfactual set AT the freeze."""
        from mironba.world.authoring import Draft, validate_draft

        draft = Draft(sentence="LeBron James signs with the Warriors",
                      kind="stipulated", event="signing",
                      seed_date="2026-07-06",
                      moves=[{"player_name": "LeBron James",
                              "from_team": "", "to_team": "Warriors"}])
        validate_draft(draft)
        assert not draft.errors, draft.errors
        assert draft.signing_routes


class TestTheSubjectCollisionIsRefused:
    def test_naming_the_signee_as_decision_subject_is_refused(self, bound):
        """run_branch removes SUBJECT from the signable pool - including in
        the UNSEEDED run - so this configuration silently empties the very
        comparison the run exists to make. Caught by writing the first
        signing scenario exactly this way."""
        from mironba.sim.signing_seed import build_signing

        scenario, league_mod, league = bound
        original = getattr(league_mod, "SUBJECT", None)
        league_mod.SUBJECT = scenario.stipulation["signing"]["player_id"]
        try:
            with pytest.raises(SystemExit, match="decision_subject"):
                build_signing(scenario, league, league_mod)
        finally:
            league_mod.SUBJECT = original


class TestWhoElseWantedHim:
    def test_the_pursuit_list_comes_from_the_unseeded_run(self):
        """The seeded run excludes the signee from the pool, so it has no
        contest for him. Every pursuer must therefore be evidence from the
        run WITHOUT the seed."""
        if not (RUN / "manifest.json").is_file():
            pytest.skip(REGENERATE)
        manifest = json.loads(
            (RUN / "manifest.json").read_text(encoding="utf-8"))
        pursuit = manifest.get("pursuit") or []
        assert pursuit, "the signee was contested; the list should not be empty"
        for row in pursuit:
            assert row["amount"] > 0
            assert row["route"]
        winners = [r for r in pursuit if r["won_him_without_the_seed"]]
        assert len(winners) == 1, "exactly one team wins him in the null"

    def test_the_winner_did_something_else_with_the_seed(self):
        if not (RUN / "manifest.json").is_file():
            pytest.skip(REGENERATE)
        manifest = json.loads(
            (RUN / "manifest.json").read_text(encoding="utf-8"))
        winner = next(r for r in manifest["pursuit"]
                      if r["won_him_without_the_seed"])
        assert winner["did_instead"] or winner["missed_out_on"], (
            "the team that won him in the null must differ somewhere in the "
            "seeded run, or the seed changed nothing for it")
