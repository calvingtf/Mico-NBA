"""The freeze, enforced rather than promised.

A backtest is only worth running if the simulator cannot see the answer. The
failure mode is not dishonesty, it is convenience — one call site that reads
the whole evidence file because filtering was someone else's job. These tests
are the reason the partition is structural.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from mironba.world.evidence import (
    POST,
    PRE,
    SCORING_UNLOCK,
    ConditionalCommitment,
    EvidenceError,
    EvidenceItem,
    EvidenceLedger,
    load_ledger,
    redact_after,
)

DOCS = Path(__file__).resolve().parents[1] / "evidence" / "lebron-2026"
BACKTEST = "lebron-2026"
FREEZE = date(2026, 7, 6)
PACKAGE = Path(__file__).resolve().parents[1] / "mironba"


@pytest.fixture
def ledger() -> EvidenceLedger:
    return load_ledger(DOCS, BACKTEST, FREEZE)


class TestThePartitionHolds:
    def test_no_post_freeze_item_is_reachable_from_world_state(self, ledger):
        """The requirement, stated directly."""
        for item in ledger.world_state():
            assert item.phase == PRE, f"{item.id} is POST but reached world state"
            assert item.date <= FREEZE, (
                f"{item.id} is dated {item.date}, after the freeze {FREEZE}"
            )

    def test_world_state_and_ground_truth_are_disjoint_and_complete(self, ledger):
        pre = {i.id for i in ledger.world_state()}
        post = {i.id for i in ledger.ground_truth(unlock=SCORING_UNLOCK)}
        assert not pre & post
        assert pre | post == {i.id for i in ledger.items}
        assert post, "a backtest with no post-freeze outcome cannot be scored"

    def test_ground_truth_refuses_without_the_token(self, ledger):
        with pytest.raises(EvidenceError, match="not an input"):
            ledger.ground_truth(unlock="")
        with pytest.raises(EvidenceError):
            ledger.ground_truth(unlock="please")

    def test_open_conditionals_are_pre_freeze_only(self, ledger):
        assert ledger.open_conditionals(), "the fork needs at least one"
        for conditional in ledger.open_conditionals():
            assert conditional.phase == PRE
            assert conditional.date <= FREEZE

    def test_the_outcomes_the_task_names_are_all_post(self, ledger):
        """LeBron to Philadelphia, Green's re-signing and the Golden State
        retentions are outcomes, not inputs. If any of them drifted into PRE
        the backtest would be scoring its own input."""
        post = {i.id for i in ledger.ground_truth(unlock=SCORING_UNLOCK)}
        for required in ("LBJ-07", "LBJ-08", "GSW-10", "GSW-12", "GSW-13",
                         "GSW-14", "GSW-18"):
            assert required in post, f"{required} must be ground truth"


class TestMislabellingIsCaught:
    def _item(self, when: date, phase: str) -> EvidenceItem:
        return EvidenceItem(
            id="X-1", date=when, fact="f", source="s", url="http://x",
            retrieved=date(2026, 7, 31), phase=phase,
        )

    def test_a_post_freeze_date_labelled_pre_fails_validation(self):
        """The one error the file exists to prevent. A row dated after the
        freeze but marked PRE would flow straight into world state, and every
        accessor downstream trusts the label."""
        ledger = EvidenceLedger(BACKTEST, FREEZE, items=[
            self._item(date(2026, 7, 24), PRE)
        ])
        problems = ledger.validate()
        assert problems and "should be POST" in problems[0]

    def test_a_pre_freeze_date_labelled_post_also_fails(self):
        """The other direction is not harmless either: it withholds a legitimate
        input and makes the simulator look worse than it is."""
        ledger = EvidenceLedger(BACKTEST, FREEZE, items=[
            self._item(date(2026, 6, 1), POST)
        ])
        assert any("should be PRE" in p for p in ledger.validate())

    def test_the_real_file_is_consistent(self, ledger):
        assert ledger.validate() == []

    def test_a_fact_cannot_predate_its_own_retrieval(self):
        ledger = EvidenceLedger(BACKTEST, FREEZE, items=[
            EvidenceItem(id="X-1", date=date(2026, 7, 1), fact="f", source="s",
                         url="http://x", retrieved=date(2025, 1, 1), phase=PRE)
        ])
        assert any("before the fact" in p for p in ledger.validate())

    def test_an_item_without_a_source_is_refused(self):
        with pytest.raises(EvidenceError, match="source and a url"):
            EvidenceItem(id="X", date=FREEZE, fact="f", source="", url="",
                         retrieved=FREEZE, phase=PRE)

    def test_a_conditional_needs_both_halves(self):
        with pytest.raises(EvidenceError, match="condition and a commitment"):
            ConditionalCommitment(
                id="C", subject="GSW", condition="", commitment="does a thing",
                reported_by="x", date=FREEZE, url="http://x", retrieved=FREEZE,
            )


class TestNothingElseCanReadTheAnswer:
    def test_only_eval_may_name_the_unlock(self):
        """Greps the package. The token is not a secret — it is a marker that
        makes reading the answer greppable, which is worth nothing unless
        something actually greps."""
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if path.parts[-2:][0] == "eval" or "evidence.py" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            if "SCORING_UNLOCK" in text or "ground_truth" in text:
                offenders.append(str(path.relative_to(PACKAGE.parent)))
        assert not offenders, (
            "post-freeze ground truth is reachable from: "
            + ", ".join(offenders)
            + ". Only eval/ may read the answer."
        )

    def test_agents_never_touch_the_evidence_file(self):
        """agents/ has no business reading evidence even in its PRE half. An
        agent is handed a world state; assembling one is the simulator's job,
        and an agent that could read the ledger could read past the freeze."""
        for path in (PACKAGE / "agents").rglob("*.py"):
            assert "world.evidence" not in path.read_text(encoding="utf-8"), (
                f"{path.name} imports evidence"
            )

    def test_the_simulator_reads_only_the_pre_freeze_api(self):
        """sim/ may build a world state — that is what M4's branch runner does
        — but only through world_state() and open_conditionals(). This was
        widened from a blanket ban once the pending-decision primitive existed;
        the ban had been standing in for this narrower rule."""
        for path in (PACKAGE / "sim").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "world.evidence" not in text:
                continue
            for banned in ("ground_truth", "SCORING_UNLOCK"):
                assert banned not in text, f"{path.name} reads the answer"

    def test_no_simulator_module_carries_the_real_outcome_figures(self):
        """The leak that actually happened. The branch scorer began life inside
        sim/branch.py with the real post-freeze salaries as a module-level
        dict, one import away from the planner that must never see them."""
        outcomes = ("27678571", "27_678_571", "3876529", "3_876_529",
                    "19512195", "19_512_195")
        for package in ("sim", "agents"):
            for path in (PACKAGE / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for figure in outcomes:
                    assert figure not in text, (
                        f"{path.name} hardcodes the post-freeze figure {figure}; "
                        "ground truth belongs in eval/"
                    )


class TestTheIngestLeaksToo:
    """The evidence file is not the only way a post-freeze fact can arrive.

    Our own transaction log runs to 2026-07-09, three days past the freeze. A
    world state built from the snapshot without a date filter would hand the
    simulator a signing that happened after the moment it is supposed to be
    reasoning from — and it would look like ordinary roster data.
    """

    SNAPSHOT = (
        Path(__file__).resolve().parents[1]
        / "mironba" / "data" / "snapshots" / "bbref-2025-26" / "transactions.csv"
    )

    def test_the_snapshot_really_does_contain_post_freeze_rows(self):
        import csv

        if not self.SNAPSHOT.is_file():
            pytest.skip("snapshot not rebuilt; see README")
        with self.SNAPSHOT.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        after = [r for r in rows if date.fromisoformat(r["date"]) > FREEZE]
        assert after, (
            "this test documents a hazard; if the snapshot no longer runs past "
            "the freeze, re-check whether the freeze filter is still needed"
        )

    def test_redact_after_removes_them(self):
        import csv

        if not self.SNAPSHOT.is_file():
            pytest.skip("snapshot not rebuilt; see README")
        with self.SNAPSHOT.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        kept = redact_after(rows, FREEZE, key="date")
        assert kept, "the filter must not empty the table"
        assert len(kept) < len(rows)
        assert all(date.fromisoformat(r["date"]) <= FREEZE for r in kept)

    def test_redact_after_is_inclusive_of_the_freeze_day(self):
        rows = [{"date": "2026-07-06"}, {"date": "2026-07-07"}]
        kept = redact_after(rows, FREEZE, key="date")
        assert [r["date"] for r in kept] == ["2026-07-06"]


class TestTheFileIsSubstantial:
    def test_the_evidence_file_is_hand_curated_and_sized(self, ledger):
        assert 30 <= len(ledger.items) <= 50, (
            f"{len(ledger.items)} items; the brief asks for roughly 30-50"
        )

    def test_every_item_carries_full_provenance(self, ledger):
        for item in ledger.items:
            assert item.url.startswith("http"), f"{item.id} has no usable url"
            assert item.source, f"{item.id} has no source"
            assert item.retrieved, f"{item.id} has no retrieval date"
            assert item.fact.strip(), f"{item.id} has no fact"

    def test_urls_are_not_obviously_malformed(self, ledger):
        pattern = re.compile(r"^https?://[\w.-]+/\S*$")
        for item in ledger.items:
            assert pattern.match(item.url), f"{item.id}: {item.url}"
