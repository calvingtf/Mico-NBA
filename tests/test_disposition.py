"""Disposition is read off the standings, and stays that way.

The first version of this module set its bands from the value model's 10.5-win
separation threshold. That was the wrong error bar: 10.5 is uncertainty on a
*counterfactual roster delta*, and disposition depends on record and games back
on the freeze date, which are completed facts. The consequence was concrete —
23 of 30 teams came back AMBIGUOUS and the deadline simulation proposed nothing
between middling teams.

These tests pin both halves of the fix: the bands are measured, and the value
model cannot get back in.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from mironba.models import disposition as disp
from mironba.world.calendar import calendar_for

SOURCE = Path(disp.__file__)


def test_disposition_never_consults_the_value_model():
    """No import path from disposition to the value model, at any depth.

    Asserted on the source rather than by monkeypatching, because the failure
    being guarded against is someone reaching for a projection when an observed
    quantity is what the question calls for. A runtime spy would pass for a
    module that imports the value model and happens not to call it on the
    tested input.
    """
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{a.name}" for a in node.names)

    banned = {"value", "win_delta", "compare", "delta_error"}
    offenders = sorted(
        name for name in imported
        if any(part in banned for part in name.split("."))
    )
    assert not offenders, (
        f"disposition imports {offenders}. Disposition is observed record, not "
        "a projection — see the module docstring for what happened last time."
    )


def test_bands_are_not_the_delta_error_threshold():
    """The bands must not have drifted back to the value model's numbers."""
    from mironba.models.compare import MEASURED_DELTA_SD

    assert disp.SELLER_GAMES_BACK < MEASURED_DELTA_SD
    assert disp.BUYER_GAMES_AHEAD < MEASURED_DELTA_SD
    assert disp.BAND_PROVENANCE.startswith("measured")


def test_a_dominant_team_is_a_buyer():
    """Oklahoma City at 40-9 was once reported as fifteen games out.

    The games-back formula already carries its sign for a team inside the cut;
    negating it again inverted the whole league. This is that bug, pinned.
    """
    season = "2024-25"
    freeze = calendar_for(season).deadline
    sides = disp.disposition(season, freeze)
    if not sides:
        pytest.skip("game log snapshot not present")

    best = max(sides.values(), key=lambda d: d.standing.win_pct)
    assert best.side == disp.BUYER, (
        f"{best.team} at {best.standing.wins}-{best.standing.losses} came back "
        f"{best.side} ({best.games_back:+.1f} games back)"
    )
    worst = min(sides.values(), key=lambda d: d.standing.win_pct)
    assert worst.side == disp.SELLER


def test_the_bands_leave_a_real_market_on_both_sides():
    """A deadline with 4 buyers and 3 sellers is a broken calibration.

    Not a claim that any particular team is labelled right — a claim that the
    thresholds produce a market. The old ones did not.
    """
    sides = disp.disposition("2024-25", calendar_for("2024-25").deadline)
    if not sides:
        pytest.skip("game log snapshot not present")
    counts: dict[str, int] = {}
    for value in sides.values():
        counts[value.side] = counts.get(value.side, 0) + 1
    assert counts.get(disp.BUYER, 0) >= 8
    assert counts.get(disp.SELLER, 0) >= 5
    assert counts.get(disp.AMBIGUOUS, 0) < 15


def test_ambiguous_teams_are_not_inactive():
    assert disp.AMBIGUOUS_ACTS is True


def test_standings_ignore_games_after_the_date():
    season = "2024-25"
    early = disp.standings_on(season, date(2024, 12, 1))
    late = disp.standings_on(season, calendar_for(season).deadline)
    if not early or not late:
        pytest.skip("game log snapshot not present")
    for team, standing in early.items():
        assert standing.games_played <= late[team].games_played
    assert sum(s.games_played for s in early.values()) < sum(
        s.games_played for s in late.values()
    )


def test_conference_membership_matches_the_snapshot():
    sides = disp.disposition("2024-25", calendar_for("2024-25").deadline)
    if not sides:
        pytest.skip("game log snapshot not present")
    assert len(disp.EAST) == 15
    assert disp.EAST <= set(sides)
    assert len(set(sides) - disp.EAST) == 15
