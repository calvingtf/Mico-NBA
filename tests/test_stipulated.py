"""The stipulated path: asserted event, rules-first, unfalsifiable and says so."""

from __future__ import annotations

import pytest

from mironba.world.scenario import ScenarioError, load_scenario


@pytest.fixture(autouse=True)
def _rebind_the_suite_scenario():
    """These tests bind curry; the rest of the suite assumes lebron-2026."""
    yield
    import mironba.sim.league as league_mod

    league_mod.bind_scenario(load_scenario("lebron-2026"))


class TestTheStipulatedScenario:
    def test_the_canonical_scenario_loads_as_stipulated(self):
        sc = load_scenario("curry-lakers-2026")
        assert sc.kind == "stipulated"
        assert not sc.branches and not sc.actual_branch
        assert sc.stipulation["players"], "the asserted event must be declared"

    def test_a_stipulated_scenario_may_not_declare_branches(self):
        from dataclasses import replace

        sc = load_scenario("curry-lakers-2026")
        with pytest.raises(ScenarioError, match="no branches"):
            replace(sc, branches=("a", "b"), actual_branch="a")

    def test_a_stipulation_without_an_event_is_refused(self):
        from dataclasses import replace

        sc = load_scenario("curry-lakers-2026")
        with pytest.raises(ScenarioError, match="no stipulation"):
            replace(sc, stipulation={})


class TestRulesComeFirst:
    def test_the_declared_package_passes_the_validator(self):
        """The stipulated trade must be LEGAL under rules/ - the runner never
        bypasses the validator to make a premise happen."""
        import mironba.sim.league as league_mod
        from mironba.rules.constants import environment_for
        from mironba.sim.stipulated import build_trade, validate_trade

        sc = load_scenario("curry-lakers-2026")
        league_mod.bind_scenario(sc)
        league = league_mod.LeagueState.load()
        trade = build_trade(sc, league)
        assert validate_trade(trade, environment_for(sc.next_season)).legal

    def test_an_illegal_stipulation_is_refused_not_bypassed(self):
        """Curry-for-Doncic straight up fails apron matching; the runner must
        exit 1 and never reach the reaction."""
        import mironba.sim.league as league_mod
        from mironba.rules.constants import environment_for
        from mironba.sim.stipulated import build_trade, validate_trade

        sc = load_scenario("curry-lakers-2026")
        league_mod.bind_scenario(sc)
        league = league_mod.LeagueState.load()
        bad = dict(sc.stipulation)
        bad["players"] = [
            {"player_id": "curryst01", "from": "GSW", "to": "LAL"},
            {"player_id": "doncilu01", "from": "LAL", "to": "GSW"},
        ]
        object.__setattr__(sc, "stipulation", bad)
        trade = build_trade(sc, league)
        assert not validate_trade(trade, environment_for(sc.next_season)).legal

    def test_salaries_come_from_the_snapshot_not_the_yaml(self):
        sc = load_scenario("curry-lakers-2026")
        for move in sc.stipulation["players"]:
            assert "salary" not in move, (
                "a stipulated trade may not restate a salary; the snapshot "
                "is the source and the validator is the judge"
            )


class TestTheLabel:
    def test_the_output_is_labelled_unfalsifiable_in_those_words(self):
        from mironba.sim.stipulated import UNFALSIFIABLE

        assert "UNFALSIFIABLE" in UNFALSIFIABLE
        assert "demonstration" in UNFALSIFIABLE
        assert "not a measurement" in UNFALSIFIABLE

    def test_a_pending_scenario_is_refused_by_the_stipulated_runner(self):
        from mironba.sim.stipulated import main

        with pytest.raises(SystemExit, match="pending_decision"):
            main(["--scenario", "lebron-2026"])


class TestTheSeedHolds:
    def test_no_stipulated_player_changes_teams_in_any_scenario(self):
        """The invariant, enumerated: every stipulated yaml's movers stay
        put through the whole reaction. The arrivals union pours real
        arrivals into the signable pool - right for a pending scenario,
        wrong once a player's team is stipulated - and both existing
        stipulated scenarios violated this before the exclusion existed
        (PHI signed Grimes; BOS won a league-wide contest for Giannis)."""
        from pathlib import Path

        import mironba.sim.league as league_mod
        from mironba.sim.stipulated import apply_trade, build_trade

        config_dir = Path(__file__).resolve().parents[1] / "configs" / "branch"
        stipulated_ids = [
            path.stem for path in sorted(config_dir.glob("*.yaml"))
            if "kind: stipulated" in path.read_text(encoding="utf-8")
        ]
        assert stipulated_ids, "enumeration found no stipulated scenarios"

        for sid in stipulated_ids:
            sc = load_scenario(sid)
            league_mod.bind_scenario(sc)
            league_mod.TEAMS = league_mod._all_teams()
            league = league_mod.LeagueState.load()
            trade = build_trade(sc, league)
            apply_trade(league, trade)
            movers = {p.player_id for p in trade.players}
            results, _, _ = league_mod.run_branch(
                "stipulated", league, [], stipulated=movers)
            for team in league_mod.TEAMS:
                leaked = movers & set(results[team].signed)
                assert not leaked, (
                    f"{sid}: {team} signed stipulated mover(s) "
                    f"{sorted(leaked)} during the reaction"
                )
