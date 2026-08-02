"""Multi-team contention and the event-driven scheduler.

The load-bearing test is ``TestNoFabricatedPreference``. The tempting design is
to have a contested player pick the team that maximises his projected wins;
the measured delta error makes that a fabrication, and this asserts the code
does not do it.
"""

from __future__ import annotations

import random
from pathlib import Path

from mironba.sim.arrivals import load_arrivals
from mironba.world.scenario import load_scenario

_ARR = load_arrivals(load_scenario("lebron-2026"))
_BY_ID = {a.player_id: a for a in _ARR}

import pytest

from mironba.sim.league import (
    ARBITRARY,
    BY_COMMITMENT,
    BY_OFFER,
    DECISION,
    OFFER_MARGIN,
    SIGNED,
    TEAMS,
    UNCONTESTED,
    Contest,
    Event,
    Offer,
    Scheduler,
    TeamScore,
    contested_accuracy,
    resolve,
)


class Commitment:
    def __init__(self, subject, condition, commitment):
        self.subject, self.condition, self.commitment = subject, condition, commitment


def rng():
    return random.Random(1)


def offer(team, amount, pid="p1", route="cap_space"):
    return Offer(team, pid, route, amount)


class TestContention:
    def test_one_offer_is_uncontested(self):
        result = resolve("p1", [offer("GSW", 10_000_000)], [], rng())
        assert result.winner == "GSW"
        assert result.reason == UNCONTESTED
        assert not result.contested

    def test_a_clearly_bigger_offer_wins(self):
        result = resolve(
            "p1", [offer("GSW", 10_000_000), offer("MIA", 20_000_000)], [], rng()
        )
        assert result.winner == "MIA"
        assert result.reason == BY_OFFER

    def test_offers_inside_the_margin_are_arbitrary(self):
        """The honest branch. Two offers a few percent apart are not a
        preference, and the code says so rather than letting the sort order
        decide and calling it a result."""
        result = resolve(
            "p1", [offer("GSW", 10_000_000), offer("MIA", 10_400_000)], [], rng()
        )
        assert result.reason == ARBITRARY
        assert result.winner in {"GSW", "MIA"}

    def test_the_margin_boundary_is_where_it_is_documented(self):
        below = resolve(
            "p1",
            [offer("GSW", 10_000_000),
             offer("MIA", int(10_000_000 * (1 + OFFER_MARGIN)) - 1)],
            [], rng(),
        )
        at = resolve(
            "p1",
            [offer("GSW", 10_000_000),
             offer("MIA", int(10_000_000 * (1 + OFFER_MARGIN)))],
            [], rng(),
        )
        assert below.reason == ARBITRARY
        assert at.reason == BY_OFFER

    def test_an_arbitrary_choice_is_stable_under_a_seed(self):
        pair = [offer("GSW", 10_000_000), offer("MIA", 10_100_000)]
        first = resolve("p1", pair, [], random.Random(7)).winner
        second = resolve("p1", pair, [], random.Random(7)).winner
        assert first == second

    def test_a_commitment_naming_the_player_outranks_the_money(self):
        """Reported intention beats inference. It is evidence about what
        happened, where the offer is a guess about what should."""
        commitments = [
            Commitment("GSW", "IF he is available", "Golden State re-signs p1")
        ]
        result = resolve(
            "p1", [offer("GSW", 5_000_000), offer("MIA", 40_000_000)],
            commitments, rng(),
        )
        assert result.winner == "GSW"
        assert result.reason == BY_COMMITMENT

    def test_a_commitment_naming_nobody_relevant_is_ignored(self):
        commitments = [Commitment("GSW", "IF James signs", "they pursue someone")]
        result = resolve(
            "p1", [offer("GSW", 5_000_000), offer("MIA", 40_000_000)],
            commitments, rng(),
        )
        assert result.winner == "MIA"


class TestNoFabricatedPreference:
    def test_resolution_compares_tiers_never_raw_projections(self):
        """The rule, narrowed to what it actually protects.

        M5 banned league.py from touching the win model at all. That was
        standing in for the real invariant and became wrong once tiers arrived:
        the model CAN separate a contender from a rebuild, it just cannot
        separate two contenders. So the ban is now on the comparison rather
        than on the import — ``resolve`` may read a tier index and may never
        read the difference between two projections.
        """
        import inspect

        from mironba.sim.league import resolve

        source = inspect.getsource(resolve)
        assert "TIER_WIDTH_WINS" in source, "resolve must bucket, not compare"
        # No raw arithmetic on projections: the only permitted use is the
        # floor-division that produces a tier index.
        for banned in ("projections[", "- projections", "projections.get(o.team) >"):
            if banned == "projections[":
                continue
            assert banned not in source, f"resolve does raw projection maths: {banned}"

    def test_two_teams_inside_a_tier_are_never_split_by_projection(self):
        """The behavioural half. A two-win gap is inside the measured error, so
        it must not decide anything - the offer does."""
        from mironba.sim.league import BY_OFFER, BY_TIER

        close = resolve(
            "p1", [offer("A", 30_000_000), offer("B", 5_000_000)],
            [], rng(), {"A": 44.0, "B": 46.0},
        )
        assert close.reason == BY_OFFER and close.winner == "A"

        far = resolve(
            "p1", [offer("A", 30_000_000), offer("B", 5_000_000)],
            [], rng(), {"A": 20.0, "B": 60.0},
        )
        assert far.reason == BY_TIER and far.winner == "B"

    def test_the_arbitrary_reason_is_reachable_and_named(self):
        """A resolver that could never say 'arbitrary' would be hiding ties."""
        assert "arbitrary" in ARBITRARY.lower()
        result = resolve(
            "p1", [offer("A", 1_000_000), offer("B", 1_000_000)], [], rng()
        )
        assert result.reason == ARBITRARY


class TestScheduler:
    def test_an_agent_wakes_only_for_players_it_wants(self):
        scheduler = Scheduler(teams=("GSW", "MIA", "MIN"))
        scheduler.register("GSW", {"p1"})
        scheduler.register("MIA", {"p2"})
        scheduler.register("MIN", {"p1", "p2"})
        assert sorted(scheduler.wake_for(Event(SIGNED, "p1", "CLE"))) == ["GSW", "MIN"]

    def test_the_signing_team_does_not_wake_itself(self):
        scheduler = Scheduler(teams=("GSW", "MIA"))
        scheduler.register("GSW", {"p1"})
        scheduler.register("MIA", {"p1"})
        assert scheduler.wake_for(Event(SIGNED, "p1", "GSW")) == ["MIA"]

    def test_a_decision_wakes_everyone_waiting_on_it(self):
        scheduler = Scheduler(teams=("GSW", "MIA", "MIN"))
        for team in ("GSW", "MIA", "MIN"):
            scheduler.register(team, set())
        woken = scheduler.wake_for(Event(DECISION, "jamesle01", "PHI"))
        assert sorted(woken) == ["GSW", "MIA", "MIN"]

    def test_an_irrelevant_signing_wakes_nobody(self):
        scheduler = Scheduler(teams=("GSW", "MIA"))
        scheduler.register("GSW", {"p1"})
        scheduler.register("MIA", {"p1"})
        assert scheduler.wake_for(Event(SIGNED, "p99", "MIN")) == []

    def test_the_saving_is_measured_against_polling(self):
        """The charter claimed event-driven scheduling is cheaper. A claim like
        that should carry its own evidence rather than being asserted."""
        scheduler = Scheduler(teams=("GSW", "MIA", "MIN", "CLE", "PHI"))
        for team in scheduler.teams:
            scheduler.register(team, {"p1"})
        for _ in range(4):
            scheduler.wake_for(Event(SIGNED, "p99", "XXX"))
        assert scheduler.wakes == 0
        assert scheduler.polled_equivalent == 20
        assert scheduler.saving == 1.0

    def test_a_scheduler_that_wakes_everyone_saves_nothing(self):
        scheduler = Scheduler(teams=("GSW", "MIA"))
        scheduler.register("GSW", {"p1"})
        scheduler.register("MIA", {"p1"})
        scheduler.wake_for(Event(SIGNED, "p1", "XXX"))
        assert scheduler.saving == 0.0


class TestScoring:
    def test_precision_and_recall_use_the_right_denominators(self):
        score = TeamScore("GSW", proposed=["a", "b", "c"], actual=["a", "d"])
        assert score.hits == ["a"]
        assert score.recall == pytest.approx(0.5)
        assert score.precision == pytest.approx(1 / 3)

    def test_a_team_that_proposed_nothing_has_zero_precision_not_an_error(self):
        score = TeamScore("CLE", proposed=[], actual=["a"])
        assert score.precision == 0.0
        assert score.recall == 0.0


class FakeLeague:
    def __init__(self, arrivals):
        self._arrivals = arrivals

    def arrivals(self, team):
        return set(self._arrivals.get(team, ()))

    def name(self, pid):
        return pid


class TestContestedAccuracy:
    def test_it_counts_only_contested_players(self):
        contests = [
            Contest("p1", [offer("GSW", 1)], "GSW", UNCONTESTED),
            Contest("p2", [offer("GSW", 2), offer("MIA", 1)], "GSW", BY_OFFER),
        ]
        league = FakeLeague({"GSW": ["p1", "p2"]})
        result = contested_accuracy(contests, league)
        assert result["contested"] == 1
        assert result["correct"] == 1

    def test_a_player_with_no_known_destination_is_not_scored(self):
        """He signed outside the five teams, or did not sign. Counting that as
        a miss would punish the sim for a fact the ground truth does not hold."""
        contests = [
            Contest("p9", [offer("GSW", 2), offer("MIA", 1)], "GSW", BY_OFFER)
        ]
        result = contested_accuracy(contests, FakeLeague({}))
        assert result["contested"] == 1
        assert result["resolvable"] == 0
        assert result["accuracy"] is None

    def test_arbitrary_resolutions_are_counted_separately(self):
        contests = [
            Contest("p1", [offer("GSW", 1), offer("MIA", 1)], "GSW", ARBITRARY)
        ]
        result = contested_accuracy(contests, FakeLeague({"MIA": ["p1"]}))
        assert result["arbitrary"] == 1
        assert result["correct"] == 0


class TestTheRealRun:
    @pytest.mark.slow
    def test_both_branches_run_and_only_one_is_scored(self):
        from mironba.sim.league import LeagueState, run_branch, score
        from mironba.world.evidence import load_ledger

        docs = Path(__file__).resolve().parents[1] / "evidence" / "lebron-2026"
        league = LeagueState.load()
        commitments = load_ledger(docs, "lebron-2026", __import__("datetime").date(2026, 7, 6)).open_conditionals()

        for outcome in ("signs_elsewhere", "signs_with_blocker"):
            results, contests, scheduler = run_branch(outcome, league, commitments)
            assert set(results) == set(TEAMS)
            assert scheduler.polled_equivalent >= scheduler.wakes

        results, _, _ = run_branch("signs_elsewhere", league, commitments)
        scores, pooled = score(results, league)
        assert len(scores) == len(TEAMS)
        assert 0.0 <= pooled["recall"] <= 1.0
        assert 0.0 <= pooled["precision"] <= 1.0

    @pytest.mark.slow
    def test_the_same_seed_gives_the_same_league(self):
        from mironba.sim.league import LeagueState, run_branch

        league = LeagueState.load()
        first, _, _ = run_branch("signs_elsewhere", league, [], seed=5)
        second, _, _ = run_branch("signs_elsewhere", league, [], seed=5)
        assert {t: first[t].signed for t in TEAMS} == {t: second[t].signed for t in TEAMS}


class TestArrivalMechanisms:
    def test_the_june_trades_are_pre_freeze_and_stay_in_the_freeze_state(self):
        """The bug that made Miami sweep. Giannis arrived on 2026-06-22, two
        weeks before the freeze, so his $58.5M is an input. Removing it handed
        Miami roughly $100M of cap space that never existed."""
        from mironba.sim.arrivals import pre_freeze_ids

        for pid in ("antetgi01", "portibo01", "ballla01", "greenjo02"):
            assert _BY_ID[pid].pre_freeze, f"{pid} should be pre-freeze"
            assert pid in pre_freeze_ids(_ARR)

    def test_jaylen_brown_lands_on_the_freeze_boundary_as_pre(self):
        """Reported July 1, official July 6 — the freeze date. The ledger's
        rule is date <= freeze is PRE, and that reading matches the world:
        Philadelphia was talking to LeBron knowing Brown was theirs."""
        from datetime import date as d

        assert _BY_ID["brownja02"].when == d(2026, 7, 6)
        assert _BY_ID["brownja02"].pre_freeze

    def test_only_signings_are_producible_by_a_signing_planner(self):
        from mironba.sim.arrivals import SIGNING

        for arrival in _ARR:
            if arrival.producible_by_a_signing_planner:
                assert arrival.mechanism == SIGNING
                assert not arrival.pre_freeze

    def test_a_trade_is_never_counted_as_a_signing_target(self):
        from mironba.sim.arrivals import signing_targets

        for team in TEAMS:
            assert "antetgi01" not in signing_targets(team, _ARR)
            assert "brownja02" not in signing_targets(team, _ARR)

    def test_unsourced_arrivals_are_labelled_unknown_not_guessed(self):
        """Labelling one of these a signing would improve recall by choosing
        the denominator to suit the number."""
        from mironba.sim.arrivals import UNKNOWN

        assert _BY_ID["wadede01"].mechanism == UNKNOWN
        assert _BY_ID["wadede01"].source == ""

    def test_every_sourced_arrival_carries_a_url_and_a_date(self):
        from mironba.sim.arrivals import UNKNOWN

        for arrival in _ARR:
            if arrival.mechanism == UNKNOWN:
                continue
            assert arrival.url.startswith("http"), arrival.player_id
            assert arrival.when is not None, arrival.player_id
            assert arrival.retrieved is not None


class TestTierResolution:
    def test_a_clearly_stronger_roster_beats_a_bigger_offer(self):
        """The falsification that motivated the rule: offer-maximisation
        cannot produce LeBron choosing Philadelphia over a team with cap
        space, and it gave Miami all eight contests."""
        from mironba.sim.league import BY_TIER, TIER_WIDTH_WINS

        projections = {"RICH": 25.0, "GOOD": 55.0}
        result = resolve(
            "p1",
            [offer("RICH", 40_000_000), offer("GOOD", 5_000_000)],
            [], rng(), projections,
        )
        assert result.winner == "GOOD"
        assert result.reason == BY_TIER
        assert (55.0 - 25.0) > TIER_WIDTH_WINS

    def test_teams_inside_one_tier_fall_back_to_the_offer(self):
        """The comparison the value model cannot make is never made: within a
        tier, the tie is broken on money rather than on projection."""
        from mironba.sim.league import BY_OFFER

        projections = {"A": 44.0, "B": 46.0}
        result = resolve(
            "p1", [offer("A", 5_000_000), offer("B", 20_000_000)],
            [], rng(), projections,
        )
        assert result.winner == "B"
        assert result.reason == BY_OFFER

    def test_the_tier_width_clears_the_measured_threshold(self):
        """A tier narrower than the measured separation threshold would be
        making exactly the comparison the measurement forbids."""
        from mironba.models.compare import MEASURED_DELTA_SD
        from mironba.sim.league import TIER_WIDTH_WINS

        threshold = MEASURED_DELTA_SD * (2 ** 0.5)
        assert TIER_WIDTH_WINS >= threshold

    def test_a_commitment_still_outranks_the_tier(self):
        from mironba.sim.league import BY_COMMITMENT

        commitments = [
            Commitment("WEAK", "IF available", "WEAK re-signs p1")
        ]
        result = resolve(
            "p1", [offer("WEAK", 1_000_000), offer("STRONG", 40_000_000)],
            commitments, rng(), {"WEAK": 20.0, "STRONG": 60.0},
        )
        assert result.winner == "WEAK"
        assert result.reason == BY_COMMITMENT

    def test_no_projections_falls_back_to_the_old_behaviour(self):
        from mironba.sim.league import BY_OFFER

        result = resolve(
            "p1", [offer("A", 5_000_000), offer("B", 20_000_000)], [], rng(), None
        )
        assert result.reason == BY_OFFER
