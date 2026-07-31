"""Multi-team contention and the event-driven scheduler.

The load-bearing test is ``TestNoFabricatedPreference``. The tempting design is
to have a contested player pick the team that maximises his projected wins;
the measured delta error makes that a fabrication, and this asserts the code
does not do it.
"""

from __future__ import annotations

import random
from pathlib import Path

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
    def test_resolution_never_consults_the_win_model(self):
        """The rule this module is most likely to break. The measured delta
        error is 7.4 wins against a 10.5-win separation threshold, so a
        projection cannot rank two destinations — and a resolver that asked it
        to would produce a confident answer with nothing behind it."""
        source = (
            Path(__file__).resolve().parents[1]
            / "mironba" / "sim" / "league.py"
        ).read_text(encoding="utf-8")
        for banned in ("win_delta", "projected_wins", "WinModel", "team_strength"):
            assert banned not in source, (
                f"league.py references {banned}; a contested player's choice "
                "must not be a win-maximisation the model cannot support"
            )

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

        docs = Path(__file__).resolve().parents[1] / "docs" / "backtests"
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
