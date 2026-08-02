"""Scenario identifiers may live in scenario files, or in the declared debt.

The enumeration found 89 scenario-bound occurrences across 14 files. Migration
is tracked, not pretended: SCENARIO_DEBT names every module still holding
identifiers, this test fails on any module NOT on that list, and the README
does not claim scenario-generality until the list is empty AND a second
scenario has run with zero code changes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mironba.world.scenario import SCENARIO_DEBT, load_scenario

ROOT = Path(__file__).resolve().parents[1]
IDENTIFIERS = ("lebron-2026", "jamesle01", "signs_with_blocker")
PACKAGES = ("sim", "world", "eval", "agents", "report")
#: scenario.py itself documents the debt; evidence_view declares the branch
#: rule and is on the debt list.
ALLOWED_ALWAYS = {"mironba/world/scenario.py"}


def offenders():
    out = {}
    debt = set(SCENARIO_DEBT) | ALLOWED_ALWAYS
    for pkg in PACKAGES:
        for f in (ROOT / "mironba" / pkg).rglob("*.py"):
            rel = f.relative_to(ROOT).as_posix()
            if rel in debt:
                continue
            text = f.read_text(encoding="utf-8")
            hits = [i for i in IDENTIFIERS if i in text]
            if hits:
                out[rel] = hits
    return out


class TestTheFence:
    def test_no_identifier_outside_scenario_files_or_declared_debt(self):
        found = offenders()
        assert not found, (
            f"scenario identifiers leaked into undeclared modules: {found}. "
            "Either migrate the module to read from the scenario object, or "
            "add it to SCENARIO_DEBT - hiding it is how 89 occurrences "
            "accumulated unenumerated."
        )

    def test_the_debt_is_real_not_stale(self):
        """Every debt entry still holds an identifier; a clean module leaves."""
        stale = []
        for rel in SCENARIO_DEBT:
            text = (ROOT / rel).read_text(encoding="utf-8")
            if not any(i in text for i in IDENTIFIERS):
                stale.append(rel)
        assert not stale, f"paid-off debt still listed: {stale}"

    def test_the_scenario_declares_its_own_freeze_rationale(self):
        sc = load_scenario("lebron-2026")
        assert "moratorium" in sc.freeze_rationale.lower()

    def test_no_default_scenario(self):
        from mironba.world.scenario import ScenarioError

        with pytest.raises(ScenarioError, match="declared:"):
            load_scenario("nonexistent-2027")

    def test_partition_comes_from_the_scenario_freeze(self):
        sc = load_scenario("lebron-2026")
        ledger = sc.ledger()
        assert not ledger.validate()
        assert all(i.date <= sc.freeze for i in ledger.world_state())
