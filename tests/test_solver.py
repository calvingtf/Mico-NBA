"""The deterministic package solver.

The load-bearing test is ``test_solver_and_validator_never_disagree``. Every
other test here checks a behaviour; that one checks the invariant the whole
architecture rests on — if the solver can emit a package the validator rejects,
then illegal packages are representable again and M1.5 bought nothing.
"""

from __future__ import annotations

import random
import time
from datetime import date

import pytest

from mironba.rules.cap import ApronTier
from mironba.rules.constants import environment_for
from mironba.rules.solver import (
    Asset,
    Package,
    TradeIntent,
    constraint_explanation,
    solve,
)
from mironba.rules.trade_validator import (
    ReSignStatus,
    Severity,
    TeamTradeState,
    Trade,
    Verdict,
    validate_trade,
)

SEASON = "2024-25"
ENV = environment_for(SEASON)
TRADE_DATE = date(2025, 2, 6)


def assets(*rows: tuple[str, int]) -> dict[str, Asset]:
    return {pid: Asset(pid, pid.upper(), salary) for pid, salary in rows}


def team(team_id: str, salary: int, roster: int = 14) -> TeamTradeState:
    return TeamTradeState(team_id=team_id, team_salary=salary, roster_count=roster)


def run(
    intent: TradeIntent,
    own: dict[str, Asset],
    theirs: dict[str, Asset],
    *,
    own_salary: int = 150_000_000,
    partner_salary: int = 150_000_000,
    **kwargs,
):
    return solve(
        intent,
        own=own,
        theirs=theirs,
        own_team=team("LAL", own_salary),
        partner_team=team("GSW", partner_salary),
        season=SEASON,
        trade_date=TRADE_DATE,
        **kwargs,
    )


class TestFindsLegalPackages:
    def test_a_simple_matched_swap_is_found(self):
        own = assets(("a", 20_000_000), ("b", 5_000_000))
        theirs = assets(("t", 20_500_000))
        result = run(TradeIntent(("t",), ("a", "b")), own, theirs)
        assert result.satisfiable
        assert ("a",) in [p.send_player_ids for p in result.packages]

    def test_every_returned_package_is_legal(self):
        own = assets(*[(f"p{i}", 3_000_000 + i * 2_000_000) for i in range(8)])
        theirs = assets(("t", 24_000_000))
        result = run(TradeIntent(("t",), tuple(own)), own, theirs)
        assert result.satisfiable
        for package in result.packages:
            assert package.verdict in (Verdict.APPROVED, Verdict.UNDETERMINED)

    def test_the_receive_side_is_exactly_the_intent(self):
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_500_000), ("u", 9_000_000))
        result = run(TradeIntent(("t",), ("a",)), own, theirs)
        assert all(p.receive_player_ids == ("t",) for p in result.packages)


class TestExclusionsAndPriority:
    def test_an_excluded_player_is_never_sent(self):
        own = assets(("star", 30_000_000), ("filler", 29_000_000))
        theirs = assets(("t", 30_000_000))
        result = run(
            TradeIntent(("t",), ("star", "filler"), excluded_player_ids=("star",)),
            own,
            theirs,
        )
        for package in result.packages:
            assert "star" not in package.send_player_ids

    def test_exclusion_beats_a_contradictory_tradeable_list(self):
        """A model that lists a player as both tradeable and excluded gets the
        cautious reading, not the convenient one."""
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_000_000))
        result = run(
            TradeIntent(("t",), ("a",), excluded_player_ids=("a",)), own, theirs
        )
        assert not result.satisfiable
        assert result.binding_constraint == "NO_TRADEABLE_ASSETS"

    def test_priority_orders_otherwise_equal_packages(self):
        own = assets(("keep", 20_000_000), ("spare", 20_000_000))
        theirs = assets(("t", 20_500_000))
        result = run(
            TradeIntent(("t",), ("keep", "spare"), priority=("spare", "keep")),
            own,
            theirs,
        )
        assert result.packages[0].send_player_ids == ("spare",)

    def test_fewer_bodies_outranks_priority(self):
        """A GM parting with one player prefers that to three, whatever the
        stated ordering says about which three."""
        own = assets(("big", 21_000_000), ("x", 7_000_000), ("y", 7_000_000), ("z", 7_000_000))
        theirs = assets(("t", 21_500_000))
        result = run(
            TradeIntent(("t",), ("x", "y", "z", "big"), priority=("x", "y", "z", "big")),
            own,
            theirs,
        )
        assert len(result.packages[0].send_player_ids) == 1


class TestNoLegalPackage:
    def test_it_names_the_binding_constraint(self):
        own = assets(("a", 2_000_000))
        theirs = assets(("t", 50_000_000))
        result = run(TradeIntent(("t",), ("a",)), own, theirs, own_salary=180_000_000)
        assert not result.satisfiable
        assert result.binding_constraint == "SALARY_MATCH"

    def test_it_reports_the_closest_miss_in_dollars(self):
        """"No legal package" is not actionable. "You were $2.1M short" is."""
        own = assets(("a", 2_000_000))
        theirs = assets(("t", 50_000_000))
        result = run(TradeIntent(("t",), ("a",)), own, theirs, own_salary=180_000_000)
        assert "short of" in result.closest_miss
        assert "$" in result.closest_miss

    def test_an_unknown_target_is_distinguished_from_an_illegal_one(self):
        """Different failures need different fixes, so they get different names."""
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_000_000))
        result = run(TradeIntent(("ghost",), ("a",)), own, theirs)
        assert result.binding_constraint == "NO_VALID_TARGET"

    def test_the_explanation_tells_the_agent_what_to_change(self):
        own = assets(("a", 2_000_000))
        theirs = assets(("t", 50_000_000))
        result = run(TradeIntent(("t",), ("a",)), own, theirs, own_salary=180_000_000)
        text = constraint_explanation(result)
        assert "SALARY_MATCH" in text
        assert "Revise the intent" in text

    def test_a_satisfiable_result_has_no_explanation_to_give(self):
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_500_000))
        assert constraint_explanation(run(TradeIntent(("t",), ("a",)), own, theirs)) == ""


class TestApronRules:
    def test_a_second_apron_team_cannot_aggregate(self):
        """The CBA forbids it outright, so no multi-player package may appear."""
        own = assets(("a", 10_000_000), ("b", 10_000_000), ("c", 10_000_000))
        theirs = assets(("t", 19_000_000))
        salary = ENV.second_apron + 8_000_000
        result = run(
            TradeIntent(("t",), ("a", "b", "c")), own, theirs, own_salary=salary
        )
        for package in result.packages:
            assert len(package.send_player_ids) == 1

    def test_apron_matching_is_stricter_than_below(self):
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 24_000_000))
        below = run(TradeIntent(("t",), ("a",)), own, theirs, own_salary=140_000_000)
        apron = run(
            TradeIntent(("t",), ("a",)),
            own,
            theirs,
            own_salary=ENV.first_apron + 5_000_000,
        )
        assert below.satisfiable
        assert not apron.satisfiable


class TestDeterminismAndTruncation:
    def test_the_same_intent_yields_the_same_shortlist(self):
        own = assets(*[(f"p{i}", 4_000_000 + i * 1_500_000) for i in range(10)])
        theirs = assets(("t", 22_000_000))
        intent = TradeIntent(("t",), tuple(own), priority=tuple(reversed(list(own))))
        first = run(intent, own, theirs)
        second = run(intent, own, theirs)
        assert [p.send_player_ids for p in first.packages] == [
            p.send_player_ids for p in second.packages
        ]

    def test_a_large_space_says_so_rather_than_truncating_silently(self):
        own = assets(*[(f"p{i}", 4_000_000 + i * 500_000) for i in range(12)])
        theirs = assets(("t", 20_000_000))
        result = run(TradeIntent(("t",), tuple(own)), own, theirs, limit=5)
        assert len(result.packages) <= 5
        if result.feasible_found > 5:
            assert result.truncated
            assert "showing" in result.explain()

    def test_the_return_cap_is_respected(self):
        own = assets(*[(f"p{i}", 4_000_000 + i * 500_000) for i in range(12)])
        theirs = assets(("t", 20_000_000))
        assert len(run(TradeIntent(("t",), tuple(own)), own, theirs, limit=3).packages) <= 3


class TestSolverAndValidatorAgree:
    """The invariant the architecture rests on."""

    def test_solver_and_validator_never_disagree(self):
        """Generated intents, every returned package re-validated from scratch.

        The solver calls validate_trade internally, so this could only fail if
        it built a different Trade than the one it validated — which is exactly
        the bug that would reintroduce illegal packages without anyone noticing.
        """
        rng = random.Random(20260731)
        checked = 0
        for _ in range(120):
            n_own = rng.randint(2, 7)
            own = assets(
                *[(f"o{i}", rng.randrange(1_000_000, 40_000_000)) for i in range(n_own)]
            )
            theirs = assets(
                *[
                    (f"t{i}", rng.randrange(1_000_000, 45_000_000))
                    for i in range(rng.randint(1, 2))
                ]
            )
            own_salary = rng.randrange(120_000_000, 200_000_000)
            partner_salary = rng.randrange(120_000_000, 200_000_000)
            intent = TradeIntent(
                target_player_ids=tuple(theirs),
                tradeable_asset_ids=tuple(own),
                excluded_player_ids=tuple(
                    p for p in own if rng.random() < 0.15
                ),
            )
            result = run(
                intent,
                own,
                theirs,
                own_salary=own_salary,
                partner_salary=partner_salary,
            )
            for package in result.packages:
                trade = Trade(
                    season=SEASON,
                    trade_date=TRADE_DATE,
                    teams=(team("LAL", own_salary), team("GSW", partner_salary)),
                    players=tuple(
                        PlayerAssetFor(own[p], "LAL", "GSW")
                        for p in package.send_player_ids
                    )
                    + tuple(
                        PlayerAssetFor(theirs[p], "GSW", "LAL")
                        for p in package.receive_player_ids
                    ),
                )
                validation = validate_trade(trade, ENV)
                errors = [
                    f for f in validation.findings if f.severity is Severity.ERROR
                ]
                assert not errors, (
                    f"solver returned a package the validator rejects: "
                    f"{package.send_player_ids} -> {package.receive_player_ids}; "
                    f"{[str(f) for f in errors]}"
                )
                checked += 1
        assert checked > 50, f"only {checked} packages exercised; weak assurance"

    def test_an_undetermined_package_is_returned_not_discarded(self):
        """BYC is an unknown, not an illegality.

        Every snapshot-derived player is UNKNOWN for re-sign status, so
        discarding UNDETERMINED would make the solver return nothing on real
        data and would hide the verdict M0 built a third value for.
        """
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_500_000))
        result = run(
            TradeIntent(("t",), ("a",)),
            own,
            theirs,
            re_sign_status=ReSignStatus.UNKNOWN,
        )
        assert result.satisfiable
        assert result.packages[0].verdict is Verdict.UNDETERMINED

    def test_a_resolved_package_comes_back_approved(self):
        own = assets(("a", 20_000_000))
        theirs = assets(("t", 20_500_000))
        result = run(
            TradeIntent(("t",), ("a",)),
            own,
            theirs,
            re_sign_status=ReSignStatus.NOT_RE_SIGNED,
        )
        assert result.packages[0].verdict is Verdict.APPROVED


def PlayerAssetFor(asset: Asset, from_team: str, to_team: str):
    from mironba.rules.trade_validator import PlayerAsset

    return PlayerAsset(
        player_id=asset.player_id,
        name=asset.name,
        salary=asset.salary,
        from_team=from_team,
        to_team=to_team,
        re_sign_status=ReSignStatus.UNKNOWN,
    )


class TestLatency:
    def test_a_realistic_intent_solves_in_well_under_a_second(self):
        """Reported now rather than discovered at M3, where a scheduler fans
        this out across many agents per tick."""
        own = assets(*[(f"p{i}", 1_500_000 + i * 2_500_000) for i in range(15)])
        theirs = assets(("t", 35_000_000))
        started = time.monotonic()
        result = run(TradeIntent(("t",), tuple(own)), own, theirs)
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, f"solver took {elapsed:.2f}s on a 15-asset roster"
        assert result.elapsed_s < 1.0

    def test_the_worst_case_search_is_bounded(self):
        """C(n, <=4) with the matching bound pruning before validation."""
        own = assets(*[(f"p{i}", 1_000_000 + i * 400_000) for i in range(20)])
        theirs = assets(("t", 12_000_000))
        started = time.monotonic()
        run(TradeIntent(("t",), tuple(own)), own, theirs, own_salary=130_000_000)
        assert time.monotonic() - started < 2.0
