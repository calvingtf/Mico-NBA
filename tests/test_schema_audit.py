"""Two rules that came out of entry #74, enforced rather than remembered.

1. Every model call's field count is known and its disposition declared.
2. A classifier's accuracy is never reported without its predicted class
   distribution, because a degenerate predictor and a mediocre one score
   identically against a balanced null and need opposite responses.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from mironba.eval.classifier_score import report, score
from mironba.llm.schema_audit import (BY_DESIGN, CALL_AUDIT, CANDIDATE,
                                      MEASURED, SINGLE, SchemaNotFound,
                                      by_purpose, field_counts)

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "mironba"


def _purposes_in_source() -> set[str]:
    """Every ``purpose=`` literal passed to a model call, from the source."""
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "purpose" and isinstance(
                        keyword.value, ast.Constant):
                    found.add(keyword.value.value)
    return found


class TestEveryCallIsAudited:
    def test_the_registry_covers_every_purpose_in_the_codebase(self):
        """A new model call must declare how many fields it asks for and
        why. Adding one without an entry fails here - the same fence as the
        writer registry, DERIVED_FACTS and the findings dispositions."""
        in_source = _purposes_in_source()
        # measurement-only call sites are exempt: they exist to be run by
        # hand against a declared set, never inside the product's flow
        in_source = {p for p in in_source if not p.endswith("_measurement")}
        registered = set(by_purpose())
        assert in_source - registered == set(), (
            f"unregistered model calls: {sorted(in_source - registered)}")

    def test_the_registry_names_no_call_that_does_not_exist(self):
        in_source = _purposes_in_source()
        assert set(by_purpose()) - in_source == set()

    def test_every_schema_named_in_the_registry_is_findable(self):
        """A registry entry pointing at a schema that is not there returns
        no useful count - and must say so rather than return zero, because
        'not found' and 'no fields' are both 0 and call for opposite
        responses. Caught when this registry named the RSS curation schema
        CurationDraft; it is called Draft."""
        for row in CALL_AUDIT:
            try:
                fields, nested = field_counts(row)
            except SchemaNotFound as exc:
                pytest.fail(str(exc))
            assert fields + nested > 0, f"{row.purpose}: no fields found"

    def test_a_missing_schema_raises_rather_than_returning_zero(self):
        from dataclasses import replace

        with pytest.raises(SchemaNotFound):
            field_counts(replace(CALL_AUDIT[0], schema="NoSuchSchemaAnywhere"))

    def test_every_disposition_is_declared_and_reasoned(self):
        for row in CALL_AUDIT:
            assert row.disposition in (MEASURED, CANDIDATE, SINGLE, BY_DESIGN)
            assert len(row.note) > 60, f"{row.purpose}: reason too thin"

    def test_a_multi_field_call_is_never_left_unclassified(self):
        """A call asking for several fields is either measured, declared
        whole on purpose, or named as an unmeasured candidate. What it may
        not be is unexamined."""
        for row in CALL_AUDIT:
            fields, nested = field_counts(row)
            if fields + nested > 1:
                assert row.disposition in (MEASURED, CANDIDATE, BY_DESIGN)


class TestAccuracyNeverTravelsAlone:
    def test_a_constant_predictor_is_named_degenerate(self):
        pairs = [("trade", "trade")] * 6 + [("signing", "trade")] * 6
        result = score(pairs)
        assert result.accuracy == result.majority_null
        assert result.degenerate
        assert result.classes_never_predicted == ["signing"]

    def test_a_genuinely_weak_predictor_is_not_called_degenerate(self):
        """The distinction the whole module exists for: same accuracy, both
        classes used, different problem, different fix."""
        pairs = ([("trade", "trade")] * 3 + [("trade", "signing")] * 3
                 + [("signing", "signing")] * 3 + [("signing", "trade")] * 3)
        result = score(pairs)
        assert result.accuracy == result.majority_null
        assert not result.degenerate
        assert "genuinely weak" in report(result)

    def test_the_report_always_carries_the_distribution(self):
        pairs = [("trade", "trade")] * 6 + [("signing", "signing")] * 6
        text = report(score(pairs), "arm")
        assert "predicted:" in text and "truth:" in text
        assert "12/12" in text

    def test_the_recorded_arms_reproduce_entry_74(self):
        """The rule, applied to the committed evidence."""
        path = ROOT / "bench-classifier-arms.json"
        if not path.is_file():
            pytest.skip("bench-classifier-arms.json absent")
        rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
        arm_a = score((r["truth"], r["arm_a"]) for r in rows)
        arm_b = score((r["truth"], r["arm_b"]) for r in rows)
        assert arm_a.correct == 6 and arm_a.degenerate
        assert arm_a.classes_never_predicted == ["signing"]
        assert arm_b.correct == 12 and not arm_b.degenerate
        assert arm_b.exact_binomial_p() < 0.001

    def test_the_declared_sets_stay_balanced(self):
        """Both measurement sets keep their null at 50% by construction."""
        from mironba.world.authoring import CLASSIFIER_SET, KIND_SET

        for name, declared in (("CLASSIFIER_SET", CLASSIFIER_SET),
                               ("KIND_SET", KIND_SET)):
            labels = [truth for _s, truth in declared]
            counts = {label: labels.count(label) for label in set(labels)}
            assert len(set(counts.values())) == 1, (
                f"{name} is unbalanced {counts}; the majority-class null is "
                "only 50% while it is balanced")


class TestTheMeasuredClaimIsInTheCharter:
    def test_the_charter_states_the_numbers_not_just_the_rule(self):
        """'Keep schemas small' had no evidence behind it until #74. The
        charter must carry the measurement, so a reader can tell a measured
        rule from a cautious one."""
        text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        assert re.search(r"6\s*/\s*12", text), "the inert result is missing"
        assert re.search(r"12\s*/\s*12", text), "the split result is missing"
        assert "#74" in text, "the entry it comes from is not cited"
