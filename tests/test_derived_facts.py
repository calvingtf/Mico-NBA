"""Every derivation from the 2026-27 table must be declared with a direction.

free_agent_pool() consumed a legitimate table to compute something only the
future knows, and it was found by accident three entries after it depressed a
metric to a 1/6 ceiling. This test enumerates the surface programmatically -
the writer-test pattern - so the next derivation is covered by default.
"""

from __future__ import annotations

import ast
import inspect

import pytest

import mironba.sim.league as league_module
from mironba.sim.league import DERIVED_FACTS

VALID_DIRECTIONS = {"helps", "hurts", "mixed", "cleaning+target",
                    "constructor", "repair"}


def consumers_of(module, token="contracts_2627"):
    tree = ast.parse(inspect.getsource(module))
    found = set()

    class Walker(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Attribute(self, node):
            if node.attr == token and self.stack:
                found.add(self.stack[-1])
            self.generic_visit(node)

        def visit_Name(self, node):
            if node.id == token and self.stack:
                found.add(self.stack[-1])
            self.generic_visit(node)

    Walker().visit(tree)
    return found


class TestEveryDerivationIsDeclared:
    def test_the_enumeration_is_not_empty(self):
        assert consumers_of(league_module), "enumeration broke; test is void"

    def test_every_consumer_of_the_answer_table_is_declared(self):
        undeclared = consumers_of(league_module) - set(DERIVED_FACTS)
        assert not undeclared, (
            f"functions deriving from contracts_2627 without a declared "
            f"direction: {sorted(undeclared)}. Could each be computed at the "
            "freeze from pre-freeze information? Declare it in DERIVED_FACTS "
            "either way - free_agent_pool() hid for three milestones."
        )

    def test_every_declaration_carries_a_direction_and_answer(self):
        for name, spec in DERIVED_FACTS.items():
            assert spec["direction"] in VALID_DIRECTIONS, name
            assert isinstance(spec["freeze_computable"], bool), name
            assert spec["note"], name

    def test_the_known_leaks_are_on_record(self):
        assert DERIVED_FACTS["free_agent_pool"]["direction"] == "hurts"
        assert DERIVED_FACTS["project_wins"]["direction"] == "repair"
        assert DERIVED_FACTS["freeze_state"]["direction"] == "repair"

    def test_the_repair_is_freeze_computable_and_the_leak_is_not(self):
        assert DERIVED_FACTS["expiring_pool"]["freeze_computable"]
        assert not DERIVED_FACTS["free_agent_pool"]["freeze_computable"]
