"""The narrative may state no figure the manifest does not support.

/report showed one artifact picked by globbing `runs/report-*` by NAME and
taking the newest with any successful completion in it. It never looked at
what the completion WAS, so a GM's chat answer - raw JSON, `{"answer": "I
declined the package because..."}` - rendered under the heading "recorded
output of the report agent". The page claimed one thing and showed another.

Repointing the agent at manifests fixes the reachability problem but creates
a sharper one: a manifest is numbers, and prose about numbers is exactly where
a summary invents. So the prose is checked against a closed set - every figure
the manifest states, plus the length of every list it enumerates. A count of
things the manifest lists is not a new claim; anything else is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mironba.agents.report import (LIMITATIONS, build_manifest_report,
                                   filter_claims, manifest_digest,
                                   manifest_numbers)

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
RUN = "curry-lakers-2026"

#: Numbers a sentence may contain whatever the manifest says: they are
#: English, not measurements. Kept deliberately tiny.
PROSE_NUMERALS = {"1", "2"}

#: Written-out numbers, mapped to digits before the check.
#:
#: The first version of this test read digits only, and the model writes
#: prose: the real summary said "Four trades were attributable" and "three
#: of which resolved on an arbitrary tiebreak". Every one of those happened
#: to be right, and none of them was checked - a summary claiming "fifty
#: teams signed differently" would have passed a test written to stop
#: exactly that.
WORD_NUMBERS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}


def _manifest(run_id: str = RUN) -> dict:
    path = RUNS / run_id / "manifest.json"
    if not path.is_file():
        pytest.skip(
            f"runs/{run_id} absent (runs/ is gitignored). Regenerate in ~6s: "
            f"python -m mironba.sim.stipulated --scenario {run_id} "
            f"--out runs/{run_id}/manifest.json")
    return json.loads(path.read_text(encoding="utf-8"))


def numbers_in(text: str) -> set:
    """Every figure the prose asserts, digits and words alike."""
    found = set()
    for token in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", text):
        found.add(token.replace(",", ""))
    for word in re.findall(r"[a-z]+", text.lower()):
        if word in WORD_NUMBERS:
            found.add(WORD_NUMBERS[word])
    return found


class TestTheClosedNumberSet:
    def test_a_player_id_donates_no_digits(self):
        """"curryst01" must not put 01 in the allowed set. Without the word
        boundary every id donated its suffix, the set quietly absorbed
        00-99, and the prose could have invented any small number and still
        passed the test that exists to stop it."""
        allowed = manifest_numbers({"players": [{"player_id": "curryst01"}]})
        assert "01" not in allowed
        assert allowed == {"1"}, allowed  # the list length, and nothing else

    def test_a_list_length_is_supported_even_though_no_digit_appears(self):
        """"9 generated trades" is a fact about a list of nine trades even
        though the digit 9 is nowhere in the file."""
        allowed = manifest_numbers({"trades": ["a", "b", "c"]})
        assert "3" in allowed

    def test_the_real_manifest_supports_the_figures_the_digest_states(self):
        """The digest is what the model is given. Every number in it must
        already be supported, or the prompt itself is inventing."""
        manifest = _manifest()
        allowed = manifest_numbers(manifest)
        stated = numbers_in(manifest_digest(manifest))
        assert stated <= allowed, sorted(stated - allowed)


def prose_of(report) -> str:
    """The MODEL's words only.

    Not ``report.render()``. That appends LIMITATIONS verbatim, and those
    carry measured figures from elsewhere in the project - 421 proposals,
    10.48 wins - which are not claims about this run and are not the
    narrative. Checking them against this run's manifest would fail a
    constant for being constant.
    """
    parts = []
    for summary in report.branches.values():
        parts.append(summary.what_happened)
        parts.extend(summary.consequences)
    return " ".join(parts)


class TestTheProseIntroducesNoNewFigure:
    def _check(self, text: str, manifest: dict) -> None:
        allowed = manifest_numbers(manifest) | PROSE_NUMERALS
        invented = numbers_in(text) - allowed
        assert not invented, (
            f"the narrative states figures the manifest does not support: "
            f"{sorted(invented)}")

    def test_the_deterministic_summary_invents_nothing(self):
        """The no-model path, which is what ships when no model is up."""
        manifest = _manifest()
        report = build_manifest_report(RUN, manifest, agent=None)
        self._check(prose_of(report), manifest)

    def test_a_number_written_as_a_word_is_checked_too(self):
        """The model writes prose. "Four trades were attributable" is a
        figure, and a digit-only check never looked at it."""
        manifest = _manifest()
        with pytest.raises(AssertionError, match="does not support"):
            self._check("Ninety teams signed differently.", manifest)

    def test_the_real_model_summary_passes_word_numbers_included(self):
        """The recorded run of the agent against this manifest said "Four
        trades were attributable ... three of which resolved on an arbitrary
        tiebreak". Both are supported; the point is that they are now
        CHECKED."""
        manifest = _manifest()
        self._check(
            "The engine generated 9 trades, whereas 10 trades were generated "
            "without the seed. Four trades were attributable to the seed and "
            "five were displaced by it, with the cascade depth reaching 1. "
            "Processing killed 385 attempts via the counterparty gate and 41 "
            "via the solver. All 30 teams moved, and league rules forced 2 "
            "teams to act. Eight players were contested, three of which "
            "resolved on an arbitrary tiebreak. Three teams signed "
            "differently from the unseeded run.", manifest)

    def test_a_fabricating_summary_would_be_caught(self):
        """The test has to be able to fail. A sentence with a figure the
        manifest cannot support must trip it - otherwise it is decoration."""
        manifest = _manifest()
        with pytest.raises(AssertionError, match="does not support"):
            self._check("The seed caused 987654 follow-on trades.", manifest)

    @pytest.mark.browser
    def test_the_model_written_summary_invents_nothing(self):
        """The real one. Opt-in: it is a model call of minutes."""
        from mironba.agents.report import report_client

        manifest = _manifest()
        agent, _ = report_client()
        report = build_manifest_report(RUN, manifest, agent=agent)
        self._check(prose_of(report), manifest)


class TestTheExistingFilterStillApplies:
    def test_a_prediction_shaped_sentence_is_stripped(self):
        clean, dropped = filter_claims(
            "Golden State will sign a replacement next summer.")
        assert dropped and "will sign" not in clean

    def test_a_ranking_sentence_is_stripped(self):
        clean, dropped = filter_claims(
            "This was the best move available to the Lakers.")
        assert dropped

    def test_the_manifest_prompt_forbids_the_same_shapes(self):
        from mironba.agents.report import MANIFEST_SYSTEM

        lowered = MANIFEST_SYSTEM.lower()
        assert "never a forecast" in lowered
        assert "never rank" in lowered
        assert "only the numbers given" in lowered


class TestLimitationsSurviveTheMove:
    def test_the_rendered_report_still_carries_every_line(self):
        # collapsed: render() wraps at a fixed width, so a literal match
        # would be testing the column count rather than the content
        report = build_manifest_report(RUN, _manifest(), agent=None)
        rendered = " ".join(report.render().split())
        for item in LIMITATIONS:
            assert " ".join(item.split()) in rendered
