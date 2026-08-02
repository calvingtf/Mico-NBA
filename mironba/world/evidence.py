"""Dated evidence for a backtest, split by a freeze date that code enforces.

A backtest is only worth running if the simulator cannot see the answer. The
failure mode is not dishonesty, it is convenience: an evidence file with every
fact in it, a filter applied at the call site, and one call site that forgets.
The result still produces a hit rate, and the hit rate is meaningless.

So the split is structural, in the same way ``Run`` makes a manifest
unskippable:

  * ``EvidenceLedger.world_state()`` returns PRE-freeze items only, and is the
    only accessor anything under ``sim/`` or ``agents/`` may call.
  * POST-freeze items are reachable exclusively through
    ``ground_truth(unlock=SCORING_UNLOCK)``, which requires a token that exists
    so that reading the answer is a deliberate, greppable act.
  * ``test_no_post_freeze_item_is_reachable_from_world_state`` asserts the
    partition holds, and a companion test greps the package to assert nothing
    outside ``eval/`` names the unlock at all.

The phase label is checked against the date rather than trusted. A mislabelled
row is the one error this file exists to prevent, so it is a load-bearing
assertion and not a lint.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

#: Passing this to ``ground_truth`` is the deliberate act of reading the
#: answer. It is a plain string on purpose: the protection is that it is easy
#: to grep for, not that it is hard to obtain. Anything clever here would be
#: security theatre against ourselves.
SCORING_UNLOCK = "mironba.eval.scoring: reading post-freeze ground truth"

PRE = "PRE"
POST = "POST"


class EvidenceError(ValueError):
    """The evidence file cannot be trusted as written. Never downgraded."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One dated, sourced fact."""

    id: str
    #: The date the fact is *about*, not the date it was published.
    date: date
    fact: str
    source: str
    url: str
    retrieved: date
    #: "PRE" or "POST", relative to the backtest's freeze date.
    phase: str
    #: Team and player ids this touches, for filtering a world state.
    subjects: tuple[str, ...] = ()
    #: How this was corroborated, or "" when it rests on the single source.
    verified: str = ""

    def __post_init__(self) -> None:
        if self.phase not in (PRE, POST):
            raise EvidenceError(f"{self.id}: phase must be PRE or POST, got {self.phase!r}")
        if not self.url or not self.source:
            raise EvidenceError(f"{self.id}: every item needs a source and a url")


@dataclass(frozen=True, slots=True)
class ConditionalCommitment:
    """A stated intention whose antecedent is another actor's decision.

    First-class because it is what makes the branch fork. "Golden State will
    keep a roster spot open" is not a fact about Golden State's roster; it is a
    fact about what Golden State's roster becomes *under each branch* of a
    decision nobody had made yet. Flattening it into an ordinary dated fact
    loses the antecedent, and the antecedent is the whole causal structure a
    counterfactual simulator exists to reproduce.

    Deliberately carries no probability. Nobody published one, and inventing a
    number here would put a fabricated quantity into the scoring path.
    """

    id: str
    #: Who is committing — a team id, usually.
    subject: str
    #: The branch it is conditional on, stated so a simulator can match it.
    condition: str
    #: What they said they would do if that branch happens.
    commitment: str
    reported_by: str
    date: date
    url: str
    retrieved: date
    phase: str = PRE
    #: The dated transaction this annotates, when there is one.
    anchors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.phase not in (PRE, POST):
            raise EvidenceError(f"{self.id}: phase must be PRE or POST")
        if not self.condition or not self.commitment:
            raise EvidenceError(
                f"{self.id}: a conditional needs both a condition and a "
                "commitment; one without the other is an ordinary fact and "
                "belongs in the evidence file"
            )


@dataclass(frozen=True, slots=True)
class ReportedInterest:
    """A report that a team is in on a player. Typed, because substring
    matching over prose invented a suitor: LAL entered the 'reported suitors'
    set via LBJ-01, which says his Lakers tenure is OVER - a departure fact.

    **Circularity rule.** These rows are evidence about the outcome. Once they
    seed the suitor set, suitor identification is stipulated, not predicted -
    so identification is retired as a scored metric, and what gets scored is
    downstream: who won given the set, what losers did with held capacity,
    whether conditionals fire per branch. Every row must anchor to an existing
    verified item; a row with no anchor is a new claim wearing a citation.
    """

    id: str
    team: str
    player_id: str
    date: date
    source: str
    url: str
    retrieved: date
    phase: str
    anchors: str
    note: str = ""


@dataclass
class EvidenceLedger:
    """Evidence for one backtest, partitioned by its freeze date."""

    backtest_id: str
    freeze: date
    items: list[EvidenceItem] = field(default_factory=list)
    conditionals: list[ConditionalCommitment] = field(default_factory=list)
    interest: list[ReportedInterest] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Every way the file could be lying, checked at load time."""
        problems: list[str] = []
        seen: set[str] = set()
        for item in self.items:
            if item.id in seen:
                problems.append(f"duplicate id {item.id}")
            seen.add(item.id)
            # The label must agree with the arithmetic. A row dated after the
            # freeze but marked PRE would flow straight into world state.
            expected = PRE if item.date <= self.freeze else POST
            if item.phase != expected:
                problems.append(
                    f"{item.id}: dated {item.date} against freeze {self.freeze}, "
                    f"so phase should be {expected}, not {item.phase}"
                )
            if item.retrieved < item.date:
                problems.append(
                    f"{item.id}: retrieved {item.retrieved} before the fact's "
                    f"own date {item.date}"
                )
        for conditional in self.conditionals:
            expected = PRE if conditional.date <= self.freeze else POST
            if conditional.phase != expected:
                problems.append(
                    f"{conditional.id}: phase should be {expected}"
                )
        known = {i.id for i in self.items} | {c.id for c in self.conditionals}
        for row in self.interest:
            expected = PRE if row.date <= self.freeze else POST
            if row.phase != expected:
                problems.append(f"{row.id}: phase should be {expected}")
            missing = [a for a in row.anchors.split("|") if a and a not in known]
            if missing:
                problems.append(
                    f"{row.id}: anchors {missing} do not exist; an unanchored "
                    "interest row is a new claim wearing a citation"
                )
        return problems

    # -- the only door the simulator may use ------------------------------

    def world_state(self) -> list[EvidenceItem]:
        """PRE-freeze items. The whole input a simulated world may hold."""
        return [i for i in self.items if i.phase == PRE]

    def open_conditionals(self) -> list[ConditionalCommitment]:
        """PRE-freeze conditionals — the pending decisions that fork."""
        return [c for c in self.conditionals if c.phase == PRE]

    def reported_interest(self) -> list[ReportedInterest]:
        """PRE-freeze interest rows. Inputs, and inputs only: anything scored
        against them is stipulated, not predicted."""
        return [r for r in self.interest if r.phase == PRE]

    def ground_truth_interest(self, *, unlock: str) -> list[ReportedInterest]:
        """POST-freeze interest. Scoring only, same token as ground_truth()."""
        if unlock != SCORING_UNLOCK:
            raise EvidenceError(
                "post-freeze interest is outcome evidence, not an input. "
                "Pass evidence.SCORING_UNLOCK if you are scoring."
            )
        return [r for r in self.interest if r.phase == POST]

    # -- the door that has to be opened on purpose ------------------------

    def ground_truth(self, *, unlock: str) -> list[EvidenceItem]:
        """POST-freeze items. Scoring only.

        The token buys nothing against a determined caller and is not meant to.
        What it buys is that reading the answer appears in a diff, and that a
        test can assert no module outside ``eval/`` does it.
        """
        if unlock != SCORING_UNLOCK:
            raise EvidenceError(
                "post-freeze evidence is the answer to the backtest and is not "
                "an input to it. Pass evidence.SCORING_UNLOCK if you are "
                "scoring; if you are building world state, call world_state()."
            )
        return [i for i in self.items if i.phase == POST]


def redact_after(rows: list[dict], freeze: date, *, key: str) -> list[dict]:
    """Drop rows dated after the freeze. Inclusive of the freeze day itself.

    The evidence file is not the only way a post-freeze fact can arrive. Our
    own 2025-26 transaction log runs to 2026-07-09, three days past this
    backtest's freeze, so a world state assembled straight from the snapshot
    would hand the simulator a signing from after the moment it is supposed to
    be reasoning from — and it would arrive looking like ordinary roster data
    rather than like the answer.

    Lives here rather than in ``data/`` because the freeze is a property of the
    backtest, not of the snapshot. The same snapshot is legitimate input for a
    scenario with a later freeze.
    """
    kept = []
    for row in rows:
        raw = row[key]
        when = raw if isinstance(raw, date) else date.fromisoformat(str(raw).strip())
        if when <= freeze:
            kept.append(row)
    return kept


def _row_date(row: dict, key: str, item_id: str) -> date:
    try:
        return date.fromisoformat(row[key].strip())
    except (KeyError, ValueError) as exc:
        raise EvidenceError(f"{item_id}: bad {key}: {row.get(key)!r}") from exc


def load_ledger(
    directory: Path | str, backtest_id: str, freeze: date
) -> EvidenceLedger:
    """Read the evidence and conditional files for one backtest.

    Raises on any inconsistency rather than returning a partly-trusted ledger.
    A backtest that silently drops a malformed row scores against a different
    world than the one documented.
    """
    directory = Path(directory)
    ledger = EvidenceLedger(backtest_id=backtest_id, freeze=freeze)

    interest_path = directory / f"{backtest_id}-interest.csv"
    if interest_path.is_file():
        with interest_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                ledger.interest.append(ReportedInterest(
                    id=row["id"], team=row["team"], player_id=row["player_id"],
                    date=date.fromisoformat(row["date"]), source=row["source"],
                    url=row["url"],
                    retrieved=date.fromisoformat(row["retrieved"]),
                    phase=row["phase"], anchors=row.get("anchors", ""),
                    note=row.get("note", ""),
                ))

    evidence_path = directory / f"{backtest_id}-evidence.csv"
    with evidence_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("id", "").strip():
                continue
            ledger.items.append(
                EvidenceItem(
                    id=row["id"].strip(),
                    date=_row_date(row, "date", row["id"]),
                    fact=row["fact"].strip(),
                    source=row["source"].strip(),
                    url=row["url"].strip(),
                    retrieved=_row_date(row, "retrieved", row["id"]),
                    phase=row["phase"].strip().upper(),
                    subjects=tuple(
                        s for s in row.get("subjects", "").split("|") if s
                    ),
                    verified=row.get("verified", "").strip(),
                )
            )

    conditional_path = directory / f"{backtest_id}-conditionals.csv"
    if conditional_path.is_file():
        with conditional_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if not row.get("id", "").strip():
                    continue
                ledger.conditionals.append(
                    ConditionalCommitment(
                        id=row["id"].strip(),
                        subject=row["subject"].strip(),
                        condition=row["condition"].strip(),
                        commitment=row["commitment"].strip(),
                        reported_by=row["reported_by"].strip(),
                        date=_row_date(row, "date", row["id"]),
                        url=row["url"].strip(),
                        retrieved=_row_date(row, "retrieved", row["id"]),
                        phase=row.get("phase", PRE).strip().upper(),
                        anchors=tuple(
                            a for a in row.get("anchors", "").split("|") if a
                        ),
                    )
                )

    problems = ledger.validate()
    if problems:
        raise EvidenceError(
            f"{backtest_id}: evidence file is inconsistent:\n  "
            + "\n  ".join(problems)
        )
    return ledger
