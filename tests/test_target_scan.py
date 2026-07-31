"""The pre-filter, and the prune it depends on.

Two separate claims live here and it is worth keeping them apart.

**The prune must be sound.** ``solve`` skips subsets that fail a cheap
arithmetic bound before building a ``Trade``. Nothing runs behind that prune to
catch what it drops, so a bound tighter than the validator's own silently
deletes legal packages. That is not hypothetical: the prune called
``max_incoming_salary`` with no post-trade tier, took the conservative
self-consistent answer, and threw away twelve legal Lakers packages. The
scenario then reported that the team could acquire nobody, which was an
artifact of the search and read as a fact about the roster.

**The list must carry no money.** The architecture holds because the model
cannot state or reason about a dollar figure. A target list with a salary
column would hand back exactly the arithmetic M1.5 removed, and would look
helpful while doing it.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date
from itertools import combinations

from mironba.rules.cap import ApronTier, matching_upper_bound, max_incoming_salary
from mironba.rules.constants import environment_for
from mironba.rules.solver import (
    Asset,
    FeasibleTarget,
    TradeIntent,
    UnlockAsset,
    absorbable_ceiling,
    scan_targets,
    solve,
)
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    Severity,
    TeamTradeState,
    Trade,
    validate_trade,
)

SEASON = "2024-25"
ENV = environment_for(SEASON)
TRADE_DATE = date(2025, 2, 6)


def assets(*rows: tuple[str, int]) -> dict[str, Asset]:
    return {pid: Asset(pid, pid.upper(), salary) for pid, salary in rows}


def team(team_id: str, salary: int, roster: int = 14) -> TeamTradeState:
    return TeamTradeState(team_id=team_id, team_salary=salary, roster_count=roster)


def solve_one(intent, own, theirs, *, own_salary, partner_salary=150_000_000, **kw):
    return solve(
        intent,
        own=own,
        theirs=theirs,
        own_team=team("LAL", own_salary),
        partner_team=team("GSW", partner_salary),
        season=SEASON,
        trade_date=TRADE_DATE,
        **kw,
    )


def scan(own, theirs, *, own_salary, partner_salary=150_000_000, max_assets_out=3):
    return scan_targets(
        own=own,
        theirs=theirs,
        own_team=team("LAL", own_salary),
        partner_team=team("GSW", partner_salary),
        season=SEASON,
        trade_date=TRADE_DATE,
        max_assets_out=max_assets_out,
    )


# The Lakers and Warriors as the 2024-25 snapshot has them. Real figures,
# because the bug was specific to a team sitting just under an apron and a
# synthetic fixture would not have found it.
LAL_SALARY = 187_502_042
GSW_SALARY = 176_540_943
LAL = assets(
    ("lebron", 48_728_845), ("luka", 43_031_940), ("rui", 17_000_000),
    ("dfs", 14_924_167), ("reaves", 12_976_362), ("kleber", 11_000_000),
    ("vincent", 11_000_000), ("vando", 10_714_286), ("knecht", 3_819_120),
    ("wood", 3_036_040), ("milton", 2_875_000), ("hayes", 2_463_946),
)
GSW = assets(
    ("curry", 55_761_216), ("gp2", 9_130_000), ("looney", 8_000_000),
    ("podz", 3_519_960), ("santos", 1_891_857),
)


class TestPruneSoundness:
    def _brute_force(self, own, theirs, own_salary, partner_salary, max_out):
        """Every subset, fully validated. No bound of any kind."""
        legal = set()
        for target in theirs:
            for size in range(1, max_out + 1):
                for combo in combinations(own, size):
                    trade = Trade(
                        season=SEASON,
                        trade_date=TRADE_DATE,
                        teams=(
                            team("LAL", own_salary),
                            team("GSW", partner_salary),
                        ),
                        players=tuple(
                            PlayerAsset(
                                player_id=p,
                                name=own[p].name,
                                salary=own[p].salary,
                                from_team="LAL",
                                to_team="GSW",
                                re_sign_status=ReSignStatus.UNKNOWN,
                            )
                            for p in combo
                        )
                        + (
                            PlayerAsset(
                                player_id=target,
                                name=theirs[target].name,
                                salary=theirs[target].salary,
                                from_team="GSW",
                                to_team="LAL",
                                re_sign_status=ReSignStatus.UNKNOWN,
                            ),
                        ),
                    )
                    result = validate_trade(trade, ENV)
                    if not [f for f in result.findings if f.severity is Severity.ERROR]:
                        legal.add((combo, target))
        return legal

    def test_the_prune_never_drops_a_package_the_validator_accepts(self):
        """Golden State is $1.6M under the first apron, which is where the old
        bound was wrong: the bracket table would let them take $15.75M back
        against $8M out, but that crosses the apron, so the self-consistent
        tier collapsed to a flat 100% and the bound read $8M. The true ceiling
        is $9,591,056 — enough to land one dollar below the line."""
        expected = self._brute_force(LAL, GSW, LAL_SALARY, GSW_SALARY, 4)
        assert expected, "the fixture proves nothing if brute force finds nothing"

        found = set()
        for target in GSW:
            result = solve_one(
                TradeIntent((target,), tuple(LAL)), LAL, GSW,
                own_salary=LAL_SALARY, partner_salary=GSW_SALARY,
                max_assets_out=4, limit=10_000,
            )
            for package in result.packages:
                found.add((package.send_player_ids, target))

        dropped = expected - found
        assert not dropped, (
            f"prune discarded {len(dropped)} legal package(s): {sorted(dropped)[:5]}"
        )

    def test_the_bound_is_never_below_any_tier_the_validator_might_apply(self):
        """Stated over a grid, not only through one scenario."""
        for salary in range(120_000_000, 230_000_000, 7_000_000):
            for outgoing in range(1_000_000, 60_000_000, 3_000_000):
                bound = matching_upper_bound(outgoing, salary, ENV)
                for tier in ApronTier:
                    exact = max_incoming_salary(
                        outgoing, salary, ENV, post_trade_tier=tier
                    )
                    assert bound >= exact, (
                        f"bound {bound:,} < {tier.name} limit {exact:,} "
                        f"at salary {salary:,} outgoing {outgoing:,}"
                    )


class TestAbsorbableCeiling:
    def test_the_bound_is_monotone_in_outgoing(self):
        """``absorbable_ceiling`` takes the k most expensive contracts and calls
        the result a maximum. That only holds if the bound never falls as
        outgoing salary rises."""
        for salary in (120_000_000, 165_000_000, 180_000_000, 200_000_000):
            previous = -1
            for outgoing in range(0, 80_000_000, 500_000):
                value = matching_upper_bound(outgoing, salary, ENV)
                assert value >= previous, (
                    f"bound fell from {previous:,} to {value:,} at outgoing "
                    f"{outgoing:,}, team salary {salary:,}"
                )
                previous = value

    def test_anything_above_the_ceiling_is_genuinely_unreachable(self):
        own = assets(*[(f"p{i}", 2_000_000 + i * 3_000_000) for i in range(10)])
        ceiling, _ = absorbable_ceiling(
            own, team("LAL", 160_000_000), ENV, max_assets_out=4
        )
        theirs = assets(("dear", ceiling + 5_000_000))
        result = solve_one(
            TradeIntent(("dear",), tuple(own)), own, theirs,
            own_salary=160_000_000, max_assets_out=4,
        )
        assert not result.satisfiable


class TestTheListCarriesNoMoney:
    def test_no_field_looks_like_a_price(self):
        banned = (
            "salary", "cap", "payroll", "apron", "dollar", "amount",
            "money", "cost", "price", "worth", "contract",
        )
        for field in dataclasses.fields(FeasibleTarget):
            lowered = field.name.lower()
            for token in banned:
                assert token not in lowered, (
                    f"FeasibleTarget.{field.name} looks like a money field"
                )

    def test_the_rendered_line_leaks_no_figure(self):
        """The rendered block is what actually reaches the prompt. A field name
        could stay clean while the renderer put a salary in the text."""
        rendered = FeasibleTarget("abcde01", "A Player", 7, 2).render()
        numbers = [int(n) for n in re.findall(r"\d+", rendered)]
        # The "01" of a player id and the two counts. Nothing salary-sized.
        assert all(n < 1000 for n in numbers), rendered

    def test_a_real_scan_leaks_no_figure(self):
        own = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
        theirs = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])
        block = scan(own, theirs, own_salary=155_000_000).render()
        assert all(int(n) < 1000 for n in re.findall(r"\d+", block)), block


class TestScanAgreesWithTheSolver:
    OWN = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
    THEIRS = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])

    def test_every_listed_target_really_is_acquirable(self):
        result = scan(self.OWN, self.THEIRS, own_salary=155_000_000)
        assert result.any_feasible
        for target in result.targets:
            solved = solve_one(
                TradeIntent((target.player_id,), tuple(self.OWN)),
                self.OWN, self.THEIRS, own_salary=155_000_000, max_assets_out=3,
            )
            assert solved.satisfiable, f"{target.player_id} listed but unreachable"
            assert solved.feasible_found == target.legal_package_count
            assert (
                len(solved.packages[0].send_player_ids) == target.fewest_players_out
            )

    def test_every_omitted_target_really_is_unreachable(self):
        """The converse, and the claim that makes the list worth showing at
        all. Without it the list could be sound and still useless."""
        result = scan(self.OWN, self.THEIRS, own_salary=155_000_000)
        for pid in self.THEIRS:
            if pid in result.ids:
                continue
            solved = solve_one(
                TradeIntent((pid,), tuple(self.OWN)),
                self.OWN, self.THEIRS, own_salary=155_000_000, max_assets_out=3,
            )
            assert not solved.satisfiable, f"{pid} omitted but is acquirable"


class TestEmptyIsAFinding:
    def test_an_empty_list_names_a_reason(self):
        own = assets(("scrub", 1_000_000))
        theirs = assets(("star", 50_000_000))
        result = scan(own, theirs, own_salary=200_000_000, max_assets_out=1)
        assert not result.any_feasible
        assert result.empty_reason, "an empty list must say why"

    def test_the_bound_and_the_solve_give_different_empty_reasons(self):
        """"Nobody survived the arithmetic" and "everybody survived it and then
        failed validation" are different facts about a team, and the second one
        is the interesting one."""
        own = assets(("scrub", 1_000_000))
        priced_out = scan(
            own, assets(("star", 50_000_000)), own_salary=200_000_000, max_assets_out=1
        )
        assert priced_out.survived_bound == 0
        assert "priced above" in priced_out.empty_reason


class TestLatencyIsSplit:
    def test_the_two_phases_are_timed_separately(self):
        own = assets(*[(f"p{i}", 2_000_000 + i * 3_000_000) for i in range(10)])
        theirs = assets(*[(f"t{i}", 2_000_000 + i * 5_000_000) for i in range(10)])
        result = scan(own, theirs, own_salary=150_000_000)
        assert result.prefilter_s >= 0.0
        assert result.solve_s > 0.0
        assert result.considered == len(theirs)
        assert result.survived_bound <= result.considered

    def test_the_prefilter_is_the_cheap_half(self):
        """The filter is one comparison per contract; the scan behind it is a
        full solve per survivor. If that ever inverts, the split is measuring
        something other than what it claims."""
        own = assets(*[(f"p{i}", 2_000_000 + i * 3_000_000) for i in range(12)])
        theirs = assets(*[(f"t{i}", 2_000_000 + i * 2_000_000) for i in range(14)])
        result = scan(own, theirs, own_salary=150_000_000, max_assets_out=4)
        assert result.survived_bound > 0
        assert result.prefilter_s < result.solve_s


class TestUnlockSets:
    """Which of your own contracts appear in a legal package for each target.

    M1.6 measured the gap this closes. The list said "Gary Payton, 1 way, from
    1 player out"; the model asked for Payton and put Jarred Vanderbilt — the
    one player — in its exclusion list. The count said how many bodies and
    never said which.
    """

    def test_the_unlock_set_is_exactly_the_assets_in_some_legal_package(self):
        own = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
        theirs = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])
        result = scan(own, theirs, own_salary=155_000_000)
        assert result.any_feasible
        for target in result.targets:
            solved = solve_one(
                TradeIntent((target.player_id,), tuple(own)), own, theirs,
                own_salary=155_000_000, max_assets_out=3, limit=10_000,
            )
            expected = set()
            for package in solved.packages:
                expected.update(package.send_player_ids)
            assert {u.player_id for u in target.unlocks} == expected

    def test_offering_only_the_unlock_set_is_enough(self):
        """The claim the prompt makes, asserted directly.

        An intent that offers exactly the named contracts and nothing else must
        still be satisfiable — otherwise the arm tells the model something that
        is not true, which is worse than telling it nothing.
        """
        own = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
        theirs = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])
        result = scan(own, theirs, own_salary=155_000_000)
        for target in result.targets:
            offered = tuple(u.player_id for u in target.unlocks)
            solved = solve_one(
                TradeIntent((target.player_id,), offered), own, theirs,
                own_salary=155_000_000, max_assets_out=3,
            )
            assert solved.satisfiable, (
                f"{target.player_id} is listed with unlocks {offered} but an "
                "intent offering exactly those is unsatisfiable"
            )

    def test_excluding_the_whole_unlock_set_makes_it_unsatisfiable(self):
        """The converse. If a target were still reachable after excluding every
        named contract, the set would be incomplete and the model would be
        entitled to ignore it."""
        own = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
        theirs = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])
        result = scan(own, theirs, own_salary=155_000_000)
        for target in result.targets:
            excluded = tuple(u.player_id for u in target.unlocks)
            solved = solve_one(
                TradeIntent((target.player_id,), tuple(own), excluded_player_ids=excluded),
                own, theirs, own_salary=155_000_000, max_assets_out=3,
            )
            assert not solved.satisfiable

    def test_unlock_assets_carry_no_money(self):
        banned = (
            "salary", "cap", "payroll", "apron", "dollar", "amount",
            "money", "cost", "price", "worth", "contract", "value",
        )
        for field in dataclasses.fields(UnlockAsset):
            for token in banned:
                assert token not in field.name.lower(), (
                    f"UnlockAsset.{field.name} looks like a money field"
                )
        assert {f.name for f in dataclasses.fields(UnlockAsset)} == {
            "player_id", "name"
        }

    def test_the_rendered_unlock_line_leaks_no_figure(self):
        target = FeasibleTarget(
            "abcde01", "A Player", 3, 1,
            unlocks=(UnlockAsset("wxyz02", "Some Guy"),),
        )
        rendered = target.render(with_unlocks=True)
        assert "wxyz02" in rendered and "Some Guy" in rendered
        assert all(int(n) < 1000 for n in re.findall(r"\d+", rendered)), rendered

    def test_a_real_scan_with_unlocks_leaks_no_figure(self):
        own = assets(*[(f"p{i}", 2_000_000 + i * 4_000_000) for i in range(8)])
        theirs = assets(*[(f"t{i}", 1_500_000 + i * 6_000_000) for i in range(8)])
        block = scan(own, theirs, own_salary=155_000_000).render(with_unlocks=True)
        assert all(int(n) < 1000 for n in re.findall(r"\d+", block)), block

    def test_unlocks_are_hidden_unless_asked_for(self):
        """The feasible arm must keep rendering exactly what it rendered at
        M1.6, or its column stops being a baseline."""
        target = FeasibleTarget(
            "abcde01", "A Player", 3, 1,
            unlocks=(UnlockAsset("wxyz02", "Some Guy"),),
        )
        assert "wxyz02" not in target.render()
        assert "\n" not in target.render()

    def test_the_display_cap_never_hides_a_sole_unlock(self):
        """Truncation is safe only because the head is the most usable. A
        target with one way in must always show that one."""
        from mironba.rules.solver import MAX_UNLOCKS_SHOWN

        target = FeasibleTarget(
            "abcde01", "A Player", 1, 1,
            unlocks=(UnlockAsset("only01", "Only Option"),),
        )
        assert "only01" in target.render(with_unlocks=True)
        many = FeasibleTarget(
            "abcde01", "A Player", 20, 1,
            unlocks=tuple(UnlockAsset(f"u{i:02}", f"P{i}") for i in range(20)),
        )
        rendered = many.render(with_unlocks=True)
        shown = [u for u in many.unlocks if u.player_id in rendered]
        assert len(shown) == MAX_UNLOCKS_SHOWN
        assert shown == list(many.unlocks[:MAX_UNLOCKS_SHOWN]), "head, not a sample"
        assert "and 12 other(s)" in rendered, "truncation must announce itself"

    def test_the_lakers_case_names_vanderbilt(self):
        """The specific failure M1.6 recorded, pinned as a regression."""
        result = scan_targets(
            own=LAL, theirs=GSW,
            own_team=team("LAL", LAL_SALARY), partner_team=team("GSW", GSW_SALARY),
            season=SEASON, trade_date=TRADE_DATE, max_assets_out=4,
        )
        payton = next(t for t in result.targets if t.player_id == "gp2")
        assert [u.player_id for u in payton.unlocks] == ["vando"]
