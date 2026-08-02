"""No code path writes to an evidence store without human confirmation.

The phantom sixth suitor is what automatic acceptance produces. Drafts go to a
review queue; the store has exactly one writer and it raises without an
explicit confirmed=True that only a human reviewer passes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mironba.data.ingest.rss import CurationError, append_confirmed_row
from mironba.world.scenario import load_scenario

ROOT = Path(__file__).resolve().parents[1] / "mironba"


class TestTheGate:
    def test_unconfirmed_rows_are_refused(self):
        scenario = load_scenario("lebron-2026")
        with pytest.raises(CurationError, match="phantom sixth suitor"):
            append_confirmed_row(scenario, {"kind": "reported_interest",
                                            "date": "2026-06-30"})

    def test_the_store_has_exactly_one_writer(self):
        """Grep the package for write-mode opens of evidence CSVs.

        The queue may be appended freely - it is not the store. Interest and
        conditionals files may be opened for writing only inside
        append_confirmed_row.
        """
        pattern = re.compile(
            r"""open\(\s*['"aw]|\.open\(\s*['"]a['"]|\.open\(\s*['"]w['"]"""
        )
        offenders = []
        for module in ROOT.rglob("*.py"):
            text = module.read_text(encoding="utf-8")
            if "interest.csv" not in text and "conditionals.csv" not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if ("interest.csv" in line or "conditionals.csv" in line) and \
                        "review-queue" not in line:
                    window = text.splitlines()[max(0, i - 3):i + 3]
                    if any(pattern.search(w) for w in window):
                        rel = module.relative_to(ROOT).as_posix()
                        if rel != "data/ingest/rss.py":
                            offenders.append(f"{rel}:{i}")
        assert not offenders, (
            f"evidence store opened for writing outside the gate: {offenders}"
        )

    def test_phase_comes_from_the_scenario_not_the_row(self):
        """A row cannot declare its own side of the freeze."""
        import inspect

        source = inspect.getsource(append_confirmed_row)
        assert "scenario.freeze" in source
        assert 'row["phase"]' not in source

    def test_drafts_carry_the_source_sentence(self):
        from mironba.data.ingest.rss import QUEUE_FIELDS

        assert "source_sentence" in QUEUE_FIELDS

    def test_the_limit_is_stated_in_the_module(self):
        import mironba.data.ingest.rss as rss

        assert "reachable this way" in rss.__doc__
        assert "2016" in rss.__doc__
