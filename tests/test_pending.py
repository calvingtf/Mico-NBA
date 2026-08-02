"""Pending decisions, blocks, and the cost of waiting.

The invariant worth the whole file is ``TestWaitingCosts``: a team that waits
and loses must end up worse than one that never waited, unless the reserved
capacity got used. Without it, waiting is free, every agent waits for
everything, and the simulation concludes that holding out costs nothing — which
is both false and exactly the kind of error a counterfactual simulator would
present most confidently.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.world.pending import (
    LOST,
    UNRESOLVED,
    WON,
    Block,
    OpportunityCost,
    Outcome,
    PendingDecision,
    PendingError,
    active_commitments,
    build_branches,
)


def decision(**kwargs) -> PendingDecision:
    kwargs.setdefault("decision_id", "d1")
    kwargs.setdefault("owner", "player1")
    kwargs.setdefault("question", "Where does he sign?")
    kwargs.setdefault("outcomes", (
        Outcome("signs_with_blocker", "signs with us", ("GSW",)),
        Outcome("signs_elsewhere", "signs elsewhere"),
    ))
    return PendingDecision(**kwargs)


def block(**kwargs) -> Block:
    kwargs.setdefault("team", "GSW")
    kwargs.setdefault("decision_id", "d1")
    kwargs.setdefault("awaiting_outcome", "signs_with_blocker")
    kwargs.setdefault("reserved_salary", 57_736_350)
    kwargs.setdefault("reserved_roster_spots", 1)
    return Block(**kwargs)


class Commitment:
    def __init__(self, cid, condition):
        self.id, self.condition = cid, condition


class TestTheDecision:
    def test_two_outcomes_are_required(self):
        """One outcome is a fact, not a decision, and belongs in the event log."""
        with pytest.raises(PendingError, match="at least two outcomes"):
            decision(outcomes=(Outcome("only", "the only way"),))

    def test_duplicate_outcome_keys_are_refused(self):
        with pytest.raises(PendingError, match="duplicate"):
            decision(outcomes=(Outcome("a", "x"), Outcome("a", "y")))

    def test_an_unknown_outcome_cannot_be_resolved_to(self):
        with pytest.raises(PendingError, match="no outcome"):
            decision().resolve("retires")

    def test_it_carries_no_probabilities(self):
        """Nobody published one. A made-up prior here would propagate into
        every downstream projection wearing the authority of the model."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(PendingDecision)}
        for banned in ("probability", "prior", "likelihood", "odds", "weight"):
            assert banned not in names

    def test_it_starts_unresolved(self):
        d = decision()
        assert not d.resolved
        d.resolve("signs_elsewhere")
        assert d.resolved and d.resolved_to == "signs_elsewhere"


class TestBlocksSettle:
    def test_a_block_wins_when_its_outcome_happens(self):
        d = decision()
        b = block()
        d.resolve("signs_with_blocker")
        assert b.settle(d) == WON
        assert b.capacity_used

    def test_a_block_loses_when_it_does_not(self):
        d = decision()
        b = block()
        d.resolve("signs_elsewhere")
        assert b.settle(d) == LOST
        assert not b.capacity_used

    def test_an_open_decision_leaves_the_block_unresolved(self):
        """Distinct from losing. A simulation can end mid-decision, and
        recording that as a loss would invent an outcome."""
        assert block().settle(decision()) == UNRESOLVED


class TestWaitingCosts:
    """The invariant the file exists for."""

    def test_waiting_and_losing_is_worse_than_never_waiting(self):
        cost = OpportunityCost(lost_targets=("freeagent1", "freeagent2"))
        waited_and_lost = block(opportunity_cost=cost)
        d = decision()
        d.resolve("signs_elsewhere")
        waited_and_lost.settle(d)

        never_waited = block(reserved_salary=0, reserved_roster_spots=0,
                             opportunity_cost=OpportunityCost())

        assert waited_and_lost.status == LOST
        assert not waited_and_lost.capacity_used
        # Worse: it gave up alternatives and got nothing for them.
        assert waited_and_lost.opportunity_cost.anything_lost
        assert not never_waited.opportunity_cost.anything_lost

    def test_waiting_and_winning_justifies_the_cost(self):
        cost = OpportunityCost(lost_targets=("freeagent1",))
        waited_and_won = block(opportunity_cost=cost)
        d = decision()
        d.resolve("signs_with_blocker")
        waited_and_won.settle(d)
        assert waited_and_won.capacity_used
        # The alternatives are still gone; what changed is that the
        # reservation bought something.
        assert waited_and_won.opportunity_cost.anything_lost

    def test_a_wait_with_no_alternatives_lost_is_free(self):
        """Not every wait costs something. If nothing was passed on, waiting
        and losing leaves the team where it started, and the model should not
        invent a penalty."""
        b = block(opportunity_cost=OpportunityCost())
        d = decision()
        d.resolve("signs_elsewhere")
        b.settle(d)
        assert b.status == LOST
        assert not b.opportunity_cost.anything_lost

    def test_the_cost_is_named_alternatives_not_a_number(self):
        """A number would be untraceable and would invite tuning. A list of
        players is checkable against the transaction log."""
        import dataclasses

        names = {f.name for f in dataclasses.fields(OpportunityCost)}
        assert "lost_targets" in names
        for banned in ("penalty", "cost_wins", "value", "score"):
            assert banned not in names


class TestBranches:
    def test_one_branch_per_outcome(self):
        branches = build_branches(decision(), [block()], [])
        assert [b.outcome_key for b in branches] == [
            "signs_with_blocker", "signs_elsewhere"
        ]

    def test_each_branch_settles_its_own_copy_of_the_block(self):
        """Branches must not share mutable state, or settling one would
        silently settle the other."""
        branches = build_branches(decision(), [block()], [])
        won = branches[0].blocks[0]
        lost = branches[1].blocks[0]
        assert won.status == WON and lost.status == LOST
        assert won is not lost

    def test_blocks_for_other_decisions_are_ignored(self):
        other = block(decision_id="d2")
        branches = build_branches(decision(), [other], [])
        assert all(not b.blocks for b in branches)


class TestConditionalCommitments:
    def test_an_until_commitment_is_live_in_every_branch(self):
        """It describes what a team did while the decision was open, which
        happened regardless of how it resolved. Making it conditional would
        delete the waiting behaviour from the branch where the team lost —
        the branch most worth simulating."""
        c = Commitment("COND-03", "UNTIL James declares")
        branches = build_branches(decision(), [block()], [c])
        for branch in branches:
            assert c in branch.active_commitments

    def test_an_if_we_win_commitment_is_live_only_there(self):
        c = Commitment("COND-01", "IF James signs with GSW")
        branches = build_branches(decision(), [block()], [c])
        assert c in branches[0].active_commitments
        assert c not in branches[1].active_commitments

    def test_an_if_we_lose_commitment_is_live_only_there(self):
        c = Commitment("COND-05", "IF James signs elsewhere")
        branches = build_branches(decision(), [block()], [c])
        assert c not in branches[0].active_commitments
        assert c in branches[1].active_commitments

    def test_matching_is_on_the_outcome_not_the_prose(self):
        """The condition text is a quotation from reporting and has to stay
        quotable, so it cannot double as a machine key."""
        outcome = Outcome("signs_with_blocker", "signs with us", ("GSW",))
        c = Commitment("X", "IF James signs with GSW")
        live = active_commitments([c], outcome, decision=decision())
        assert live == [c]


class TestAgainstTheRealEvidence:
    def test_the_real_conditionals_split_across_branches(self):
        from pathlib import Path

        from mironba.world.evidence import load_ledger

        docs = Path(__file__).resolve().parents[1] / "evidence" / "lebron-2026"
        ledger = load_ledger(docs, "lebron-2026", date(2026, 7, 6))
        branches = build_branches(
            PendingDecision(
                decision_id="lebron-2026-destination",
                owner="jamesle01",
                question="Where does LeBron James sign?",
                outcomes=(
                    Outcome("signs_with_blocker", "signs with Golden State", ("GSW",)),
                    Outcome("signs_elsewhere", "signs elsewhere"),
                ),
            ),
            [block(decision_id="lebron-2026-destination")],
            ledger.open_conditionals(),
        )
        # COND-03 is the UNTIL one: Golden State holds roster space while the
        # decision is open. It belongs in both branches.
        for branch in branches:
            assert "COND-03" in [c.id for c in branch.active_commitments]
        # Nothing POST-freeze leaks into either branch.
        live_ids = {c.id for b in branches for c in b.active_commitments}
        assert "COND-05" not in live_ids and "COND-06" not in live_ids
