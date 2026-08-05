"""Rules findings the reaction must answer - enumerated, not assumed.

The failure this file exists to prevent: ``validate_trade`` emitting a
finding that nothing downstream reads. A hard cap the seed trade triggered
was emitted, recorded in the manifest, rendered on the page - and ignored by
the reaction, which spent LAL $12,671,000 past its own cap. Nothing failed;
the number was simply wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mironba.rules.constants import MIN_STANDARD_ROSTER, environment_for
from mironba.rules.trade_validator import Rule, Severity
from mironba.sim.obligations import (
    BLOCKS,
    CONSUMED,
    FINDING_DISPOSITION,
    IGNORED,
    obligations_from,
    undeclared_rules,
)

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
CURATED = ("curry-lakers-2026", "giannis-knicks-2026")


class TestTheEnumerationIsExhaustive:
    def test_every_rule_has_a_declared_disposition(self):
        """Same fence as the writer registry: a new Rule member must be
        declared consumed, ignored, or blocking. Adding one without saying
        which fails here rather than being silently forgotten."""
        assert undeclared_rules() == []

    def test_no_disposition_names_a_rule_that_does_not_exist(self):
        members = {v for k, v in vars(Rule).items()
                   if not k.startswith("_") and isinstance(v, str)}
        assert set(FINDING_DISPOSITION) <= members

    def test_every_disposition_is_one_of_the_three(self):
        for rule, (disposition, reason) in FINDING_DISPOSITION.items():
            assert disposition in (BLOCKS, CONSUMED, IGNORED), rule

    def test_every_ignored_finding_states_a_reason(self):
        """An ignored finding still has to earn its reason - a bare
        'ignored' is the same forgetting in a different font."""
        for rule, (disposition, reason) in FINDING_DISPOSITION.items():
            if disposition == IGNORED:
                assert len(reason) > 40, f"{rule}: reason too thin"

    def test_the_warning_severities_are_the_ones_that_reach_a_reaction(self):
        """A finding only reaches a reaction if the trade was legal, so
        every CONSUMED rule must be one the validator can emit at WARNING
        or INFO. An ERROR-severity rule declared CONSUMED would be a claim
        about code that never runs."""
        import ast

        src = (ROOT / "mironba" / "rules" / "trade_validator.py").read_text(
            encoding="utf-8")
        severities: dict[str, set[str]] = {}
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Call) and getattr(
                    node.func, "id", "") == "Finding" and len(node.args) >= 2:
                rule_node, sev_node = node.args[0], node.args[1]
                if isinstance(rule_node, ast.Attribute) and isinstance(
                        sev_node, ast.Attribute):
                    severities.setdefault(rule_node.attr, set()).add(
                        sev_node.attr)
        for rule, (disposition, _) in FINDING_DISPOSITION.items():
            emitted = severities.get(rule, set())
            if disposition == CONSUMED:
                assert emitted & {"WARNING", "INFO"}, (
                    f"{rule} is declared CONSUMED but is only emitted at "
                    f"{sorted(emitted)}; the trade would be refused first")


class TestObligationsAreDerivedNotGuessed:
    def test_no_findings_means_no_obligations(self):
        env = environment_for("2026-27")
        assert not obligations_from([], env)

    def test_hard_cap_detail_becomes_the_ceiling(self):
        from mironba.rules.trade_validator import Finding

        env = environment_for("2026-27")
        duties = obligations_from([
            Finding(Rule.HARD_CAP, Severity.WARNING, "capped", team="LAL",
                    detail={"hard_cap": 209_015_000})], env)
        assert duties.hard_caps == {"LAL": 209_015_000}

    def test_roster_minimum_detail_becomes_a_shortfall(self):
        from mironba.rules.trade_validator import Finding

        env = environment_for("2026-27")
        duties = obligations_from([
            Finding(Rule.ROSTER_MINIMUM, Severity.WARNING, "short",
                    team="GSW", detail={"roster_after": 12})], env)
        assert duties.roster_shortfall == {"GSW": MIN_STANDARD_ROSTER - 12}


@pytest.mark.parametrize("run_id", CURATED)
class TestTheRecordedRunsHonourThem:
    """The regression, on the committed artifacts. These assertions failed
    before the wiring: LAL's manifest showed a first-apron hard cap and a
    second-apron payroll on the same page."""

    def _manifest(self, run_id: str) -> dict:
        path = RUNS / run_id / "manifest.json"
        if not path.is_file():
            pytest.skip(
                f"runs/{run_id} absent (runs/ is gitignored). Regenerate in "
                f"~6s: python -m mironba.sim.stipulated --scenario {run_id} "
                f"--out runs/{run_id}/manifest.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def test_the_run_recorded_its_obligations(self, run_id):
        assert "obligations" in self._manifest(run_id)

    def test_every_hard_cap_the_seed_imposed_was_respected(self, run_id):
        duties = self._manifest(run_id).get("obligations", {})
        respected = duties.get("hard_cap_respected", {})
        assert all(respected.values()), (
            f"{run_id}: the reaction exceeded a cap the seed imposed - "
            f"{respected}")

    def test_a_hard_capped_team_ends_within_its_cap_to_the_dollar(self, run_id):
        manifest = self._manifest(run_id)
        duties = manifest.get("obligations", {})
        reaction = manifest.get("reaction", {})
        for team, line in (duties.get("hard_caps") or {}).items():
            end = reaction[team]["committed_end"]
            assert end <= line, (
                f"{run_id}: {team} ends ${end:,} against a ${line:,} cap, "
                f"over by ${end - line:,}")

    def test_every_roster_shortfall_was_met_or_reported_unmet(self, run_id):
        """Met or explicitly unmet - never quietly dropped."""
        manifest = self._manifest(run_id)
        duties = manifest.get("obligations", {})
        discharged = {row["team"]: row for row in duties.get("discharged", [])}
        for team, short in (duties.get("roster_shortfall") or {}).items():
            row = discharged.get(team)
            assert row is not None, f"{run_id}: {team} owed {short}, no record"
            accounted = len(row.get("signed", [])) + sum(
                u.get("short_by", 0) for u in row.get("unmet", []))
            assert accounted == short, (
                f"{run_id}: {team} owed {short}, accounted {accounted}")

    def test_an_obligation_signing_names_its_rule_and_route(self, run_id):
        """A forced signing that cannot say which rule forced it is
        indistinguishable from a chosen one."""
        duties = self._manifest(run_id).get("obligations", {})
        for row in duties.get("discharged", []):
            for signing in row.get("signed", []):
                assert signing["rule"] in FINDING_DISPOSITION
                assert signing["route"]
                assert signing["salary"] > 0
