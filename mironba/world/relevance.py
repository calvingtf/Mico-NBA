"""Suitor relevance: reported interest where it exists, structure where not.

The two structural filters were built, validated and found non-discriminating
in July (cap feasibility 24/30, record precedent 30/30). Reported interest is
what identifies suitors - and because it is evidence about the outcome, any
metric scored against the set it produces is stipulated, not predicted.
"""

from __future__ import annotations

REPORTED = "reported"
STRUCTURAL = "structural"


def suitor_relevance(player_id, ledger, structural):
    """(teams, path). PRE interest rows if any name the player, else the
    structural set - never both, so the path is always attributable."""
    named = sorted({r.team for r in ledger.reported_interest()
                    if r.player_id == player_id})
    if named:
        return named, REPORTED
    return sorted(structural), STRUCTURAL
