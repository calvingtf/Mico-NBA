"""Scenario loading and staging.

Parsing is tested offline. Staging needs an ingested snapshot and skips without
one, like the rest of the data-dependent suite.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import yaml

from mironba.rules.trade_validator import ReSignStatus
from mironba.sim.scenario import (
    SCENARIO_ROOT,
    SNAPSHOT_ROOT,
    ScenarioError,
    list_scenarios,
    load_scenario,
    stage,
)

MINIMAL = {
    "id": "t",
    "season": "2024-25",
    "snapshot": "bbref-2024-25",
    "team": "lal",
    "partner": "gsw",
    "trade_date": "2025-02-06",
    "seed": "  a seed  ",
}


def write(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({**MINIMAL, **overrides}), encoding="utf-8")
    return path


class TestParsing:
    def test_team_codes_are_upcased(self, tmp_path):
        scenario = load_scenario(write(tmp_path))
        assert scenario.team == "LAL"
        assert scenario.partner == "GSW"

    def test_the_seed_is_stripped(self, tmp_path):
        assert load_scenario(write(tmp_path)).seed == "a seed"

    def test_the_date_is_a_real_date(self, tmp_path):
        assert load_scenario(write(tmp_path)).trade_date == date(2025, 2, 6)

    @pytest.mark.parametrize(
        "missing", ["id", "season", "snapshot", "team", "partner", "trade_date", "seed"]
    )
    def test_a_missing_field_names_itself(self, tmp_path, missing):
        data = {k: v for k, v in MINIMAL.items() if k != missing}
        path = tmp_path / "s.yaml"
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with pytest.raises(ScenarioError, match=missing):
            load_scenario(path)

    def test_persona_defaults_are_valid(self, tmp_path):
        persona = load_scenario(write(tmp_path)).persona
        persona.validate()

    def test_an_out_of_range_persona_is_rejected_at_load(self, tmp_path):
        with pytest.raises(ValueError):
            load_scenario(write(tmp_path, persona={"risk_tolerance": 3.0}))


class TestBYCResolution:
    def test_unresolved_is_the_default(self, tmp_path):
        byc = load_scenario(write(tmp_path)).byc
        assert byc.mode == "unresolved"
        assert byc.status is ReSignStatus.UNKNOWN

    def test_an_unknown_mode_is_rejected(self, tmp_path):
        with pytest.raises(ScenarioError, match="byc_resolution.mode"):
            load_scenario(write(tmp_path, byc_resolution={"mode": "just_assume_yes"}))

    def test_an_unsourced_assumption_is_flagged(self, tmp_path):
        """The scenario may assert BYC, but the assertion is an input like any
        other and the manifest records whether anyone checked it."""
        scenario = load_scenario(
            write(tmp_path, byc_resolution={"mode": "assume_not_re_signed"})
        )
        assert scenario.byc.is_assumption is True
        assert scenario.byc.status is ReSignStatus.NOT_RE_SIGNED


class TestShippedScenarios:
    def test_both_shipped_scenarios_parse(self):
        paths = list_scenarios()
        assert len(paths) >= 2
        for path in paths:
            load_scenario(path)

    def test_one_scenario_deliberately_leaves_byc_unresolved(self):
        """So the UNDETERMINED path stays exercised against a live model.

        It is the outcome most easily lost: a suite that only ever sees
        approved and rejected would not notice `legal` starting to return False
        for undecidable trades, and M4 would score those as correct rejections.
        """
        modes = {load_scenario(p).id: load_scenario(p).byc.mode for p in list_scenarios()}
        assert "unresolved" in modes.values()

    def test_every_shipped_scenario_records_why_its_byc_choice_was_made(self):
        for path in list_scenarios():
            assert load_scenario(path).byc.note.strip(), path


REAL_SNAPSHOT = SNAPSHOT_ROOT / "bbref-2024-25" / "contracts.csv"
requires_data = pytest.mark.skipif(
    not REAL_SNAPSHOT.exists(),
    reason=(
        "ingested tables absent; rebuild with "
        "`python -m mironba.data.ingest.build --seasons 2024-25`"
    ),
)


class TestStaging:
    @requires_data
    def test_the_context_holds_only_the_two_named_teams(self):
        staged = stage(load_scenario(SCENARIO_ROOT / "curry-to-lakers.yaml"))
        assert staged.context.team_id == "LAL"
        assert staged.context.partner_team == "GSW"
        assert staged.context.own_roster
        assert staged.context.partner_roster

    @requires_data
    def test_rosters_are_the_highest_paid_players(self):
        """Context spent on minimum contracts teaches the model nothing about
        salary matching, which is the decision being made."""
        staged = stage(load_scenario(SCENARIO_ROOT / "curry-to-lakers.yaml"))
        salaries = [p.salary for p in staged.context.own_roster]
        assert salaries == sorted(salaries, reverse=True)

    @requires_data
    def test_the_payroll_caveat_is_attached_to_the_context(self):
        staged = stage(load_scenario(SCENARIO_ROOT / "curry-to-lakers.yaml"))
        assert any("cap hits" in note for note in staged.context.notes)

    @requires_data
    def test_an_assumed_byc_resolution_is_surfaced_to_the_reader(self):
        staged = stage(load_scenario(SCENARIO_ROOT / "curry-to-lakers.yaml"))
        assert any("ASSUMED" in note for note in staged.context.notes)

    @requires_data
    def test_the_snapshot_date_travels_for_the_manifest(self):
        staged = stage(load_scenario(SCENARIO_ROOT / "curry-to-lakers.yaml"))
        assert staged.snapshot_date

    def test_a_missing_snapshot_says_how_to_rebuild(self, tmp_path):
        path = write(tmp_path, snapshot="bbref-1999-00", season="1999-00")
        with pytest.raises(ScenarioError, match="ingest.build"):
            stage(load_scenario(path))
