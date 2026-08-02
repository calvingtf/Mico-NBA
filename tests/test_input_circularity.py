"""The mirror of the output rule: no sim input computable only with POST access.

The output half exists (no scored output computable without POST access). This
is the input half, run across every input-side package rather than one
function: nothing outside eval/ may hold the scoring token or open the
ground-truth door. freeze_state() consumed the 2026-27 table for three
milestones; this fence is grep-simple, but the derived-facts registry covers
the subtler case of legitimate tables consumed for future-knowing derivations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "mironba"
INPUT_PACKAGES = ("sim", "world", "agents", "rules", "models", "data", "llm")
#: evidence.py defines the token and the gated doors; it is the lock, not a key.
DEFINING = {ROOT / "world" / "evidence.py"}
FORBIDDEN = ("SCORING_UNLOCK", ".ground_truth(", "ground_truth_interest(")


def input_modules():
    for package in INPUT_PACKAGES:
        yield from (ROOT / package).rglob("*.py")


class TestInputsCannotRequireTheAnswer:
    def test_the_enumeration_is_not_empty(self):
        assert sum(1 for _ in input_modules()) > 30

    @pytest.mark.parametrize("token", FORBIDDEN)
    def test_no_input_module_holds_the_token(self, token):
        offenders = []
        for module in input_modules():
            if module in DEFINING:
                continue
            text = module.read_text(encoding="utf-8")
            if token in text:
                offenders.append(str(module.relative_to(ROOT)))
        assert not offenders, (
            f"input-side modules reference {token!r}: {offenders}. A sim input "
            "computable only with POST access is the answer wearing an input's "
            "name - the mirror of the scored-output rule."
        )

    def test_eval_is_still_allowed_to_score(self):
        eval_text = "".join(
            f.read_text(encoding="utf-8") for f in (ROOT / "eval").rglob("*.py")
        )
        assert "SCORING_UNLOCK" in eval_text
