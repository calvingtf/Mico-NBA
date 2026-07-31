"""Decisions that have not happened yet, and the teams waiting on them.

The primitive M4 was blocked on. Everything in ``world/`` so far records what
*is*; this records what is *not yet*, which is what a counterfactual simulator
needs to branch on.

The 2026 case in one paragraph. LeBron James had not decided. Golden State held
roster space open for him and finished July as the only team in the league that
had not acquired a new player. Nothing in a roster, a payroll or an event log
expresses that: the Warriors' cap sheet at the freeze looks like a team that
simply had not signed anyone, and the reason it had not is a fact about a
decision belonging to someone else.

Three things have to be modelled together or none of them works:

**The decision.** Owned by an agent, unresolved, with N named outcomes. Not a
probability distribution — nobody published one, and a made-up prior would
propagate into every downstream number.

**The block.** A team may reserve capacity against a *named outcome*. The
reservation is what makes the waiting visible, and naming the outcome is what
lets a branch decide whether the reservation was worth anything.

**The cost of waiting.** This is the part that is easy to leave out and wrong
to leave out. A team that holds cap space for a month is not in the same
position as one that spent it — the alternatives it passed on are gone. Without
that, waiting is free, every agent waits for everything, and the simulation
says holding out is costless. ``OpportunityCost`` makes it explicit, and
``test_waiting_and_losing_is_worse_than_never_waiting`` pins the invariant that
motivates the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

#: A block that was never resolved. Distinct from a resolved-and-lost block,
#: because the sim can end mid-decision and that is not the same as losing.
UNRESOLVED = "unresolved"
WON = "won"
LOST = "lost"


class PendingError(ValueError):
    """A decision or block was described incoherently. Never defaulted."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """One way a pending decision can go."""

    key: str
    description: str
    #: Ids of the actors this outcome is about — the team a player signs with,
    #: for instance. Lets a branch match commitments without string matching.
    subjects: tuple[str, ...] = ()


@dataclass
class PendingDecision:
    """An unresolved decision owned by an agent.

    Carries no probabilities. The evidence file records what people said, not
    how likely they thought it was, and inventing a prior here would put a
    fabricated number upstream of every projection that follows.
    """

    decision_id: str
    owner: str
    question: str
    outcomes: tuple[Outcome, ...]
    opened_on: date | None = None
    resolved_to: str | None = None

    def __post_init__(self) -> None:
        if len(self.outcomes) < 2:
            raise PendingError(
                f"{self.decision_id}: a decision needs at least two outcomes; "
                "one outcome is a fact and belongs in the event log"
            )
        keys = [o.key for o in self.outcomes]
        if len(set(keys)) != len(keys):
            raise PendingError(f"{self.decision_id}: duplicate outcome keys {keys}")

    @property
    def resolved(self) -> bool:
        return self.resolved_to is not None

    def outcome(self, key: str) -> Outcome:
        for candidate in self.outcomes:
            if candidate.key == key:
                return candidate
        raise PendingError(
            f"{self.decision_id}: no outcome {key!r}; have "
            f"{[o.key for o in self.outcomes]}"
        )

    def resolve(self, key: str) -> None:
        self.outcome(key)  # raises if unknown
        self.resolved_to = key


@dataclass(frozen=True, slots=True)
class OpportunityCost:
    """What a team gave up by waiting rather than acting.

    Modelled as named alternatives that became unavailable while the block was
    held, not as an abstract penalty. A number would be untraceable and would
    invite tuning; a list of players who signed elsewhere is checkable against
    the transaction log.
    """

    #: Free agents who signed elsewhere during the wait.
    lost_targets: tuple[str, ...] = ()
    #: Free-text, for the report.
    note: str = ""

    @property
    def anything_lost(self) -> bool:
        return bool(self.lost_targets)


@dataclass
class Block:
    """A team holding capacity against a named outcome of someone else's decision."""

    team: str
    decision_id: str
    #: The outcome this reservation is *for*. A block against "signs with us"
    #: is worthless if the answer turns out to be "signs elsewhere".
    awaiting_outcome: str
    #: Dollars held open. Not spent, not committed — reserved.
    reserved_salary: int = 0
    #: Roster slots held open.
    reserved_roster_spots: int = 0
    #: Assets promised to this branch and unavailable in the meantime.
    reserved_assets: tuple[str, ...] = ()
    opportunity_cost: OpportunityCost = field(default_factory=OpportunityCost)
    status: str = UNRESOLVED

    def settle(self, decision: PendingDecision) -> str:
        """Mark this block won or lost once the decision resolves."""
        if not decision.resolved:
            self.status = UNRESOLVED
            return self.status
        self.status = WON if decision.resolved_to == self.awaiting_outcome else LOST
        return self.status

    @property
    def capacity_used(self) -> bool:
        """Whether the reservation ended up being spent on what it was for."""
        return self.status == WON

    def describe(self) -> str:
        lost = (
            f", gave up {len(self.opportunity_cost.lost_targets)} alternative(s)"
            if self.opportunity_cost.anything_lost else ""
        )
        return (
            f"{self.team} reserved ${self.reserved_salary:,} and "
            f"{self.reserved_roster_spots} roster spot(s) awaiting "
            f"'{self.awaiting_outcome}' [{self.status}]{lost}"
        )


@dataclass
class Branch:
    """One outcome of one decision, and the world that follows from it."""

    decision_id: str
    outcome_key: str
    #: Conditional commitments from the evidence file that are live here.
    active_commitments: list = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    #: Set by the caller once a branch has been simulated.
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.decision_id}={self.outcome_key}"


def active_commitments(commitments, outcome: Outcome, *, decision: PendingDecision):
    """Conditional commitments that apply in this branch.

    A commitment's condition names a branch — "IF James signs with Golden
    State", "IF James signs elsewhere", "UNTIL James declares". Matching is on
    the outcome's subjects and key rather than on the prose, because the prose
    is a quotation from reporting and must stay quotable.

    ``UNTIL`` commitments are live in **every** branch: they describe what a
    team did while the decision was open, which happened regardless of how it
    resolved. Treating them as conditional on an outcome would delete the
    waiting behaviour from the branch where the team lost, which is exactly the
    behaviour worth simulating.
    """
    live = []
    for commitment in commitments:
        condition = commitment.condition.upper()
        if condition.startswith("UNTIL"):
            live.append(commitment)
            continue
        subjects = {s.lower() for s in outcome.subjects}
        mentions_subject = any(s in condition.lower() for s in subjects)
        if outcome.key == "signs_with_blocker" and mentions_subject:
            live.append(commitment)
        elif outcome.key != "signs_with_blocker" and "elsewhere" in condition.lower():
            live.append(commitment)
    return live


def build_branches(
    decision: PendingDecision,
    blocks: list[Block],
    commitments,
) -> list[Branch]:
    """One branch per outcome, with its blocks settled and commitments attached."""
    branches = []
    for outcome in decision.outcomes:
        settled = []
        for block in blocks:
            if block.decision_id != decision.decision_id:
                continue
            copy = Block(
                team=block.team,
                decision_id=block.decision_id,
                awaiting_outcome=block.awaiting_outcome,
                reserved_salary=block.reserved_salary,
                reserved_roster_spots=block.reserved_roster_spots,
                reserved_assets=block.reserved_assets,
                opportunity_cost=block.opportunity_cost,
            )
            copy.status = WON if outcome.key == copy.awaiting_outcome else LOST
            settled.append(copy)
        branches.append(
            Branch(
                decision_id=decision.decision_id,
                outcome_key=outcome.key,
                active_commitments=list(
                    active_commitments(commitments, outcome, decision=decision)
                ),
                blocks=settled,
            )
        )
    return branches
