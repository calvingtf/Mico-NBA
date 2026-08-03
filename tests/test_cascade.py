"""The trade cascade: declared termination, gated acceptance, honest diff."""

from __future__ import annotations

import pytest

from mironba.sim.cascade import MAX_DEPTH, GeneratedTrade


@pytest.fixture(autouse=True)
def _rebind_the_suite_scenario():
    """These tests bind giannis with 30 teams; the suite assumes lebron."""
    yield
    import mironba.sim.league as league_mod
    from mironba.world.scenario import load_scenario

    league_mod.bind_scenario(load_scenario("lebron-2026"))


class TestDeclaredNotDiscovered:
    def test_the_termination_rule_is_stated_in_the_module(self):
        import mironba.sim.cascade as cascade

        assert MAX_DEPTH == 3
        doc = cascade.__doc__
        assert "Termination, declared not discovered" in doc
        assert "at most one" in doc

    def test_determinism_is_a_cost_decision_and_says_so(self):
        import mironba.sim.cascade as cascade

        assert "cost decision, not a" in cascade.__doc__
        assert "capability claim" in cascade.__doc__

    def test_the_trade_key_is_who_moved_where(self):
        a = GeneratedTrade(1, "AAA", "BBB", ("x",), ("y",), 1, 1, "t1")
        b = GeneratedTrade(2, "AAA", "BBB", ("x",), ("y",), 1, 1, "different")
        c = GeneratedTrade(1, "AAA", "BBB", ("x",), ("z",), 1, 1, "t1")
        assert a.key() == b.key(), "round/trigger must not affect diff identity"
        assert a.key() != c.key()


class TestTheCascadeIsReproducible:
    def test_same_seed_same_trades(self):
        import mironba.sim.league as league_mod
        from mironba.sim.stipulated import build_trade, react
        from mironba.world.scenario import load_scenario

        sc = load_scenario("giannis-knicks-2026")
        league_mod.bind_scenario(sc)
        league_mod.TEAMS = league_mod._all_teams()
        trade = build_trade(sc, league_mod.LeagueState.load())
        _, _, _, _, first = react(sc, league_mod, 7, seed_trade=trade)
        _, _, _, _, second = react(sc, league_mod, 7, seed_trade=trade)
        assert [t.key() for t in first.trades] == [t.key() for t in second.trades]
        assert first.killed_by_gate == second.killed_by_gate
