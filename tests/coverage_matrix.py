"""The M0 coverage matrix, parsed out of README.md.

Shared by the gate tests and by the pytest session header, so the count
printed at the top of a run and the count the gate enforces cannot drift.

Two kinds of row, and the distinction is the whole point:

* **FORMULA** — is the arithmetic right at a specific edge? Answerable now,
  with a synthetic fixture placed exactly on the boundary. These *gate* M0:
  an unchecked FORMULA cell fails the suite.
* **REALITY** — does the validator agree with a deal the league actually
  approved? Answerable only with per-team apron salary on the trade date and
  base-year-compensation status, neither of which the ingest can supply.
  These are *evidence*, not safety properties, so an unchecked REALITY cell
  is reported as deferred rather than failed — but it must be declared in the
  README's deferred list, or the gate fails anyway. Silence is the failure
  mode this guards against.
"""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"

MATRIX_ROW = re.compile(
    r"^\|\s*(?P<row_id>[a-z0-9-]+)\s*\|"           # row id
    r"\s*(?P<kind>FORMULA|REALITY)\s*\|"           # row kind
    r"(?P<label>[^|]*)\|"                          # human label
    r"\s*(?P<positive>\[[ x]\])[^|]*\|"            # positive checkbox + fixture id
    r"\s*(?P<negative>\[[ x]\])[^|]*\|",           # negative checkbox + fixture id
    re.MULTILINE,
)

#: The deferred register: everything between the heading and the next one.
DEFERRED_SECTION = re.compile(
    r"^###\s+Deferred to M4\b(?P<body>.*?)(?=^##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)

CELLS = ("positive", "negative")


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def matrix_rows() -> list[dict]:
    rows = [m.groupdict() for m in MATRIX_ROW.finditer(_readme())]
    assert rows, "no coverage matrix rows found in README.md"
    return rows


def deferred_row_ids() -> set[str]:
    """Row ids named in the README's 'Deferred to M4' section.

    Deliberately literal: a row is deferred only if someone wrote its id down
    under that heading with a reason beside it.
    """
    match = DEFERRED_SECTION.search(_readme())
    if not match:
        return set()
    return set(re.findall(r"`([a-z0-9-]+)`", match.group("body")))


def unchecked(kind: str) -> list[tuple[str, str, str]]:
    """``(row_id, cell, label)`` for every unchecked cell of ``kind``."""
    return [
        (row["row_id"], cell, row["label"].strip())
        for row in matrix_rows()
        if row["kind"] == kind
        for cell in CELLS
        if row[cell] != "[x]"
    ]


def counts() -> tuple[int, int, int, int]:
    """``(formula_cells, formula_unchecked, reality_cells, reality_unchecked)``."""
    rows = matrix_rows()
    formula = [r for r in rows if r["kind"] == "FORMULA"]
    reality = [r for r in rows if r["kind"] == "REALITY"]
    return (
        len(formula) * len(CELLS),
        len(unchecked("FORMULA")),
        len(reality) * len(CELLS),
        len(unchecked("REALITY")),
    )


def summary() -> str:
    """One line for the pytest header — the deferred count, always visible."""
    f_total, f_open, r_total, r_open = counts()
    state = "GREEN" if f_open == 0 else f"OPEN ({f_open} unchecked)"
    return (
        f"M0 coverage matrix: FORMULA {f_total - f_open}/{f_total} {state} | "
        f"REALITY {r_total - r_open}/{r_total}, {r_open} deferred to M4"
    )
