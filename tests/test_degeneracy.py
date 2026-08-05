"""Watching label fields in production instead of A/B-ing each one.

#77 killed the shortcut: `event` was inert inside the proposal schema and
`kind`, the identically-shaped field beside it, was flawless there. Schema
size predicts nothing, so soundly deciding each field would mean a
12-sentence study per field - hours each, more than the splits could save.

Degeneracy does not need ground truth. A field that only ever emits one of
its values, or never emits at all, is visible from the run records alone.
These tests pin what that check may and may not claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mironba.llm.degeneracy import (MIN_N, FieldObservation, literal_fields,
                                    scan)

ROOT = Path(__file__).resolve().parents[1]


def _obs(**kw) -> FieldObservation:
    base = dict(schema="S", field="f", allowed=("a", "b"))
    base.update(kw)
    return FieldObservation(**base)


class TestTheWatchListIsDerived:
    def test_literal_fields_are_found_without_being_declared(self):
        """A hand-maintained list of label fields rots invisibly - a field
        dropped from it simply stops being watched."""
        watched = literal_fields()
        assert ("EventKind", "event") in watched
        assert ("Proposal", "kind") in watched
        assert watched[("EventKind", "event")] == ("trade", "signing")

    def test_a_non_literal_field_is_not_watched(self):
        watched = literal_fields()
        assert ("Proposal", "decision") not in watched
        assert ("Proposal", "player_names") not in watched


class TestItRefusesToConcludeAtSmallN:
    def test_a_single_valued_field_below_the_floor_says_uninformative(self):
        obs = _obs()
        obs.counts.update({"a": MIN_N - 1})
        assert obs.single_valued
        assert "BELOW" in obs.flag and "uninformative" in obs.flag
        assert "CANDIDATE" not in obs.flag

    def test_the_floor_cites_the_case_that_set_it(self):
        """n=6 is demonstrably too few: `kind` looked degenerate through the
        stipulated half of its set and finished 12/12."""
        obs = _obs()
        obs.counts.update({"a": 6})
        assert "n=6" in obs.flag

    def test_at_or_above_the_floor_it_says_CANDIDATE_not_defect(self):
        obs = _obs()
        obs.counts.update({"a": MIN_N})
        flag = obs.flag
        assert "CANDIDATE FOR STUDY" in flag
        assert "Not a confirmed defect" in flag
        assert "labelled A/B" in flag

    def test_a_field_using_both_values_is_not_flagged(self):
        obs = _obs()
        obs.counts.update({"a": 40, "b": 1})
        assert not obs.single_valued
        assert "nothing to flag" in obs.flag


class TestOmissionIsTheSharperSignal:
    def test_asked_every_time_and_answered_never_is_named(self):
        """The #74 signature, and unlike single-valuedness it does not
        depend on the inputs having varied."""
        obs = _obs(asked=MIN_N, omitted=MIN_N)
        flag = obs.flag
        assert "OMITTED" in flag
        assert "pydantic default" in flag
        assert "does NOT depend on the inputs" in flag

    def test_omission_below_the_floor_is_still_withheld(self):
        obs = _obs(asked=1, omitted=1)
        assert "below the n=" in obs.flag
        assert "#74 signature" not in obs.flag

    def test_a_field_no_call_asked_for_claims_nothing(self):
        """Absent from every schema sent is not evidence about the field -
        it is evidence the scan has no data. The distinction matters: 69
        historical Proposal calls predate the event field entirely."""
        obs = _obs(calls=69)
        assert "UNOBSERVED" in obs.flag
        assert "Says nothing about the field" in obs.flag


class TestOutsideTheLiteralIsCountedSeparately:
    def test_an_invalid_value_does_not_count_as_a_class_seen(self):
        """A raw response outside the Literal is evidence about guided
        decoding, not a value the field legitimately uses."""
        obs = _obs()
        obs.counts.update({"a": 5})
        obs.invalid.update({"zzz": 1})
        assert obs.values_seen == ["a"]
        assert obs.single_valued
        assert obs.n == 5


class TestTheRealScan:
    def test_it_runs_over_the_recorded_runs(self):
        observations = scan()
        assert observations, "no Literal fields found at all"
        for key, obs in observations.items():
            assert obs.n == sum(obs.counts.values())
            assert set(obs.counts) <= set(obs.allowed), (
                f"{key}: a value outside the Literal was counted as a class")

    def test_the_committed_record_states_what_it_cannot_find(self):
        """Item 3: a clean distribution must not read as a correctness
        result. The artifact says so in its own body, not only in a
        docstring a reader of the JSON never opens."""
        path = ROOT / "field-distributions.json"
        if not path.is_file():
            pytest.skip("field-distributions.json absent; run "
                        "python -m mironba.llm.degeneracy")
        record = json.loads(path.read_text(encoding="utf-8"))
        assert "what_this_cannot_find" in record
        assert "CORRECT" in record["what_this_cannot_find"]
        assert record["min_n"] == MIN_N

    def test_the_module_says_what_it_cannot_do(self):
        """Documented where the check lives, per item 3."""
        import mironba.llm.degeneracy as module

        # collapsed: the sentences wrap, and a test that breaks on
        # rewrapping is testing the line width, not the claim
        doc = " ".join((module.__doc__ or "").split())
        assert "cannot tell a correct field from a wrong one" in doc
        assert "Accuracy still needs a labelled set" in doc
        assert "not a defect" in doc
