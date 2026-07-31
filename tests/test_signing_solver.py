"""The signing solver, validated against real 2026 contracts.

The trade solver had a whole milestone of measurement behind it before anyone
trusted it. This one gets checked against signings that actually happened, with
figures from the ingest rather than from reporting — which is available here
precisely because M2's backtest work verified them.

The load-bearing tests are ``TestRealSignings`` (does the solver find a route
that could have paid the real contract) and ``TestAdmissibility`` (does the
prune ever exclude something brute force would find). The second is the M1.6
lesson: the trade prune was unsound for months and silently deleted twelve
legal Lakers packages.
"""

from __future__ import annotations

import csv
import dataclasses
import re
from pathlib import Path

import pytest

from mironba.rules.constants import MAX_STANDARD_ROSTER, environment_for
from mironba.rules.signing import (
    BI_ANNUAL,
    BIRD,
    CAP_SPACE,
    EARLY_BIRD,
    MINIMUM,
    NON_BIRD,
    NON_TAXPAYER_MLE,
    NOT_MODELLED,
    ROOM_EXCEPTION,
    TAXPAYER_MLE,
    FreeAgent,
    TeamCapState,
    max_salary,
    signing_routes,
)
from mironba.rules.signing_solver import (
    FeasibleSigning,
    absorbable_ceiling,
    check_signing,
    feasible_signings,
)

ENV = environment_for("2026-27")
SNAPSHOT = Path(__file__).resolve().parents[1] / "mironba" / "data" / "snapshots"
CONTRACTS = SNAPSHOT / "bbref-contracts-2026-27" / "contract_years.csv"
PRIOR = SNAPSHOT / "bbref-2025-26" / "contracts.csv"


def team_at(salary: int, **kwargs) -> TeamCapState:
    kwargs.setdefault("roster_count", 12)
    return TeamCapState("XXX", "2026-27", committed_salary=salary, **kwargs)


def agent(**kwargs) -> FreeAgent:
    kwargs.setdefault("player_id", "p1")
    kwargs.setdefault("name", "A Player")
    kwargs.setdefault("years_of_service", 5)
    return FreeAgent(**kwargs)


class TestCapPositionDecidesTheRoutes:
    def test_a_team_with_room_signs_into_room(self):
        result = signing_routes(team_at(100_000_000), agent(), ENV)
        assert CAP_SPACE in result.route_names()
        assert ROOM_EXCEPTION in result.route_names()
        # Room and the mid-level are a fork, not a menu.
        assert NON_TAXPAYER_MLE not in result.route_names()
        assert "room exception instead" in result.blocked[NON_TAXPAYER_MLE]

    def test_an_over_cap_team_below_the_apron_gets_the_full_mid_level(self):
        result = signing_routes(team_at(180_000_000), agent(), ENV)
        assert NON_TAXPAYER_MLE in result.route_names()
        assert CAP_SPACE not in result.route_names()
        assert BI_ANNUAL in result.route_names()

    def test_above_the_first_apron_the_big_mid_level_is_gone(self):
        result = signing_routes(team_at(212_000_000), agent(), ENV)
        assert NON_TAXPAYER_MLE not in result.route_names()
        assert "first apron" in result.blocked[NON_TAXPAYER_MLE]
        assert BI_ANNUAL not in result.route_names()
        assert TAXPAYER_MLE in result.route_names()

    def test_above_the_second_apron_there_is_no_mid_level_at_all(self):
        result = signing_routes(team_at(225_000_000), agent(), ENV)
        for route in (NON_TAXPAYER_MLE, TAXPAYER_MLE, BI_ANNUAL, CAP_SPACE):
            assert route not in result.route_names()
        assert result.route_names() == (MINIMUM,)

    def test_the_minimum_reaches_every_team_at_every_position(self):
        """Why a signing scan is never empty for salary reasons, and why
        Philadelphia could add LeBron James while carrying $203M."""
        for salary in (50_000_000, 164_961_000, 205_000_000, 240_000_000):
            result = signing_routes(team_at(salary), agent(), ENV)
            assert MINIMUM in result.route_names(), f"lost at ${salary:,}"


class TestHardCapsLimitTheAmount:
    """A hard cap is not a flag beside the amount. It limits the amount.

    Reported as a flag first, which offered Golden State the full $15,044,000
    non-taxpayer mid-level while sitting at $203.5M — landing at $218.6M
    against the $209.0M hard cap that using it imposes.
    """

    def test_the_mid_level_is_cut_to_the_apron_headroom(self):
        team = team_at(205_000_000)
        result = signing_routes(team, agent(), ENV)
        route = next(r for r in result.routes if r.route == NON_TAXPAYER_MLE)
        assert route.max_first_year == ENV.first_apron - 205_000_000
        assert route.max_first_year < ENV.non_taxpayer_mle
        assert "hard cap" in route.note

    def test_a_team_at_the_apron_cannot_use_it_at_all(self):
        """At exactly the apron the tier check fires first and says so; the
        headroom check is the backstop for a team that is past it. Both are
        refusals, and asserting on the wording of one would pin an
        implementation detail rather than the rule."""
        result = signing_routes(team_at(ENV.first_apron), agent(), ENV)
        assert NON_TAXPAYER_MLE not in result.route_names()
        assert "apron" in result.blocked[NON_TAXPAYER_MLE]

    def test_the_taxpayer_mid_level_is_bounded_by_the_second_apron(self):
        team = team_at(ENV.second_apron - 2_000_000)
        result = signing_routes(team, agent(), ENV)
        route = next(r for r in result.routes if r.route == TAXPAYER_MLE)
        assert route.max_first_year == 2_000_000

    def test_a_route_with_no_hard_cap_is_not_limited(self):
        """Bird rights carry no hard cap, which is how Golden State could
        re-sign Draymond Green to $27.7M and land above the first apron."""
        team = team_at(182_711_572)
        result = signing_routes(
            team, agent(years_with_team=14, years_of_service=14), ENV
        )
        bird = next(r for r in result.routes if r.route == BIRD)
        assert bird.hard_cap is None
        assert bird.max_first_year == max_salary(ENV.salary_cap, 14)
        assert team.committed_salary + 27_678_571 > ENV.first_apron


class TestBirdRights:
    def test_rights_follow_years_with_the_team(self):
        assert agent(years_with_team=3).rights == BIRD
        assert agent(years_with_team=2).rights == EARLY_BIRD
        assert agent(years_with_team=1).rights == NON_BIRD
        assert agent(years_with_team=0).rights is None

    def test_non_bird_is_120_percent_of_prior_salary(self):
        """Reproduces Al Horford exactly: his 2025-26 salary was $5,685,000 —
        the taxpayer mid-level that season — and 120% of it is $6,822,000,
        which is his actual 2026-27 salary."""
        result = signing_routes(
            team_at(203_568_143),
            agent(years_with_team=1, prior_salary=5_685_000, years_of_service=19),
            ENV,
        )
        route = next(r for r in result.routes if r.route == NON_BIRD)
        assert route.max_first_year == 6_822_000

    def test_early_bird_is_175_percent_and_says_it_is_a_floor(self):
        result = signing_routes(
            team_at(180_000_000), agent(years_with_team=2, prior_salary=4_000_000), ENV
        )
        route = next(r for r in result.routes if r.route == EARLY_BIRD)
        assert route.max_first_year == 7_000_000
        assert "not modelled" in route.note

    def test_bird_rights_reach_the_maximum_for_the_service_tier(self):
        for years, pct in ((3, 25), (8, 30), (12, 35)):
            result = signing_routes(
                team_at(180_000_000),
                agent(years_with_team=5, years_of_service=years),
                ENV,
            )
            bird = next(r for r in result.routes if r.route == BIRD)
            assert bird.max_first_year == (ENV.salary_cap * pct) // 100


class TestRosterLimits:
    def test_a_full_roster_blocks_every_route(self):
        """Salary is not the only constraint, and a solver that only checked
        money would happily sign a sixteenth player."""
        full = signing_routes(
            team_at(100_000_000, roster_count=MAX_STANDARD_ROSTER), agent(), ENV
        )
        assert not full.any_route
        # Routes that were live on salary now cite the roster; routes that were
        # already blocked keep their own reason, which is the more useful of
        # the two and would be lost by overwriting.
        open_slot = signing_routes(team_at(100_000_000, roster_count=12), agent(), ENV)
        for route in open_slot.route_names():
            assert "roster is full" in full.blocked[route]

    def test_the_scan_says_so_rather_than_returning_an_empty_list(self):
        scan = feasible_signings(
            team_at(100_000_000, roster_count=MAX_STANDARD_ROSTER),
            [agent()], ENV,
        )
        assert not scan.any_feasible
        assert "roster is full" in scan.empty_reason
        assert "salary is not the binding constraint" in scan.empty_reason


class TestAdmissibility:
    """The M1.6 lesson: a prune may over-admit, never under-admit."""

    def test_the_ceiling_never_excludes_a_signable_player(self):
        agents = [
            agent(player_id=f"p{i}", years_of_service=i % 12,
                  years_with_team=i % 4, prior_salary=1_000_000 * (i + 1))
            for i in range(24)
        ]
        for salary in (80_000_000, 164_961_000, 195_000_000, 212_000_000, 230_000_000):
            team = team_at(salary)
            ceiling = absorbable_ceiling(team, ENV)
            for candidate in agents:
                result = signing_routes(team, candidate, ENV)
                if result.any_route:
                    assert result.max_first_year <= ceiling, (
                        f"a route pays ${result.max_first_year:,} above a "
                        f"${ceiling:,} bound at team salary ${salary:,}"
                    )

    def test_the_scan_finds_everyone_a_full_solve_finds(self):
        agents = [
            agent(player_id=f"p{i}", years_of_service=i % 15, years_with_team=i % 4)
            for i in range(30)
        ]
        for salary in (90_000_000, 180_000_000, 215_000_000, 235_000_000):
            team = team_at(salary)
            brute = {
                a.player_id for a in agents if signing_routes(team, a, ENV).any_route
            }
            scanned = set(feasible_signings(team, agents, ENV).ids)
            assert brute - scanned == set(), "the scan dropped a signable player"


class TestTheListCarriesNoMoney:
    def test_no_field_looks_like_a_price(self):
        banned = ("salary", "cap", "amount", "money", "price", "dollar", "value")
        for f in dataclasses.fields(FeasibleSigning):
            for token in banned:
                assert token not in f.name.lower(), f"FeasibleSigning.{f.name}"

    def test_the_rendered_line_leaks_no_figure(self):
        rendered = FeasibleSigning("abcde01", "A Player", 3,
                                   (NON_TAXPAYER_MLE, BI_ANNUAL, MINIMUM)).render()
        assert all(int(n) < 1000 for n in re.findall(r"\d+", rendered)), rendered

    def test_a_route_name_is_not_a_price(self):
        """The distinction the design rests on. "You could use the mid-level"
        names a lever; it does not say what the lever is worth, and the solver
        still produces the terms."""
        scan = feasible_signings(team_at(180_000_000), [agent()], ENV)
        block = scan.render()
        assert NON_TAXPAYER_MLE in block
        assert str(ENV.non_taxpayer_mle) not in block
        assert "15,044,000" not in block


@pytest.mark.skipif(not CONTRACTS.is_file(), reason="contract snapshot not built")
class TestRealSignings:
    """Against contracts that actually exist, with figures from the ingest."""

    CASES = [
        # team, player_id, name, actual, service, years_with_team, expected route
        ("PHI", "jamesle01", "LeBron James", 3_876_529, 23, 0, MINIMUM),
        ("GSW", "greendr01", "Draymond Green", 27_678_571, 14, 14, BIRD),
        ("GSW", "horfoal01", "Al Horford", 6_822_000, 19, 1, NON_BIRD),
        ("GSW", "porzikr01", "Kristaps Porzingis", 19_512_195, 11, 1, None),
        ("GSW", "bassech01", "Charles Bassey", 2_449_421, 2, 1, None),
        ("GSW", "meltode01", "De'Anthony Melton", 3_451_779, 8, 1, None),
    ]

    def _rows(self):
        with CONTRACTS.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def _prior(self, player_id: str) -> int:
        if not PRIOR.is_file():
            return 0
        with PRIOR.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row["player_id"] == player_id:
                    return int(row["salary"])
        return 0

    def _check(self, case):
        team_id, pid, name, actual, service, with_team, expected = case
        rows = self._rows()
        others = [
            r for r in rows
            if r["team_id"] == team_id and r["season"] == "2026-27"
            and r["player_id"] != pid
        ]
        team = TeamCapState(
            team_id, "2026-27",
            committed_salary=sum(int(r["salary"]) for r in others),
            roster_count=len({r["player_id"] for r in others}),
        )
        player = FreeAgent(pid, name, years_of_service=service,
                           prior_salary=self._prior(pid), years_with_team=with_team)
        return check_signing(team, player, actual, ENV, expected_route=expected)

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c[2])
    def test_the_solver_reproduces_the_signing(self, case):
        check = self._check(case)
        assert check.route_found, f"{check.player}: no route at all"
        assert check.within_maximum, (
            f"{check.player}: actual ${check.actual_salary:,} exceeds the "
            f"solver's maximum of ${check.max_first_year:,}"
        )

    def test_philadelphia_needed_the_minimum_exception(self):
        """The hard case. Philadelphia carried $203.1M — over the tax, inside
        the first apron — with Embiid, Brown and Maxey at $58.1M, $57.1M and
        $40.8M. Every exception is either unavailable or too small at that
        payroll, and the minimum is what remains."""
        check = self._check(self.CASES[0])
        assert MINIMUM in check.routes
        assert check.matched_route == MINIMUM
        assert check.actual_salary == 3_876_529

    def test_green_re_signed_above_the_apron_via_bird_rights(self):
        check = self._check(self.CASES[1])
        assert BIRD in check.routes
        assert check.max_first_year >= check.actual_salary


class TestNotModelledIsNamed:
    def test_the_gaps_are_listed_rather_than_left_implicit(self):
        """An unmodelled restriction reads as permission, which is the
        dangerous direction for a rules module."""
        assert NOT_MODELLED
        joined = " ".join(NOT_MODELLED).lower()
        for topic in ("sign-and-trade", "restricted free agency", "cap holds",
                      "rookie-scale", "two-way"):
            assert topic in joined, f"{topic} is not named in NOT_MODELLED"
