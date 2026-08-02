"""EVAL-ONLY readers of a scenario's recorded outcome.

These lived on the scenario object first, and the unlock grep rejected that:
``world/`` must not be able to reach the answer, and a method is reachability.
The store files sit in the scenario's evidence directory; only this module -
inside ``eval/`` - reads them, which keeps "who can see the ground truth"
answerable by a grep, the same property SCORING_UNLOCK enforces for the
evidence ledger's POST partition.
"""

from __future__ import annotations


def ground_truth_routes(scenario) -> dict[str, str]:
    """The actual signing routes, from the store, for scoring only."""
    return {r["player_id"]: r["route"]
            for r in scenario._data_rows("ground-truth-routes.csv")}


def answer(scenario) -> dict:
    """The scenario's known outcome (e.g. destination team), for scoring only."""
    return {r["key"]: r["value"]
            for r in scenario._data_rows("ground-truth.csv")}
