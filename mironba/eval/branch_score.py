"""Scoring a branch against what actually happened.

Lives in ``eval/`` because it reads the answer, and ``eval/`` is the only place
allowed to. The ground-truth figures come out of the evidence ledger through
``ground_truth(unlock=SCORING_UNLOCK)`` rather than being written down here —
a constant in a source file is the same leak as a constant in a world state,
and harder to notice because it looks like configuration.

That separation was not the original design. The scorer started inside
``sim/branch.py`` with the real salaries as a module-level dict, next to the
planner that must never see them. Nothing read them wrongly, but nothing
prevented it either, and the M2 test that greps for exactly this caught it.

Per-move, never aggregated. An average over five moves hides which of them the
model got wrong, and the misses are the whole informational content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mironba.rules.signing import BIRD, MINIMUM, NON_BIRD
from mironba.rules.signing_solver import check_signing
from mironba.world.evidence import SCORING_UNLOCK, load_ledger

DOCS = Path(__file__).resolve().parents[2] / "docs" / "backtests"

#: Which route each actual signing used. This is a *reading* of the contract,
#: not a figure: the salaries themselves are pulled from the evidence file.
#: Recorded here because the evidence file states outcomes in prose and the
#: route is an interpretation of them — Green's Bird rights and Horford's
#: 120%-of-prior are both derivable, but derivation is not extraction.
ACTUAL_ROUTES = {
    "greendr01": BIRD,
    "horfoal01": NON_BIRD,
    "porzikr01": NON_BIRD,
    "bassech01": MINIMUM,
    "meltode01": NON_BIRD,
}

_MONEY = re.compile(r"\$([\d,]{7,})")
#: A figure immediately followed by the season it applies to. Evidence text
#: routinely lists several years of a contract, and the first year is the one
#: a signing route has to cover.
_MONEY_FOR_SEASON = re.compile(r"\$([\d,]{7,})\s+for\s+(\d{4}-\d{2})")


def actual_salaries(
    backtest: str = "lebron-2026",
    freeze: date = date(2026, 7, 6),
    season: str = "2026-27",
) -> dict[str, int]:
    """Post-freeze salaries, read through the unlock.

    Parsed out of the evidence file's POST partition rather than restated, so
    the number that gets scored against is the same number that carries a
    source URL and a retrieval date. Where an item names a player and a figure,
    that pairing is the record.
    """
    ledger = load_ledger(DOCS, backtest, freeze)
    found: dict[str, int] = {}
    for item in ledger.ground_truth(unlock=SCORING_UNLOCK):
        text = f"{item.fact} {item.verified}"
        # Season-tagged figures first. Taking max() over every number in the
        # text scored Horford against his 2027-28 salary of $7,163,100 rather
        # than the $6,822,000 he actually signed for, which then failed to fit
        # under Non-Bird and reported a route miss that was entirely the
        # scorer's own doing.
        tagged = {
            year: int(amount.replace(",", ""))
            for amount, year in _MONEY_FOR_SEASON.findall(text)
        }
        if season in tagged:
            amount = tagged[season]
        else:
            amounts = [int(m.replace(",", "")) for m in _MONEY.findall(text)]
            if not amounts:
                continue
            # No season tag: the first figure quoted is the signing figure.
            amount = amounts[0]
        for subject in item.subjects:
            if subject in ACTUAL_ROUTES and subject not in found:
                found[subject] = amount
    return found


@dataclass
class MoveScore:
    player: str
    player_id: str
    retained_actual: bool
    retained_sim: bool
    route_actual: str
    route_sim: str | None
    salary_actual: int
    max_sim: int
    within_max: bool

    @property
    def route_hit(self) -> bool:
        return self.route_sim == self.route_actual

    @property
    def retain_hit(self) -> bool:
        return self.retained_sim == self.retained_actual

    def line(self) -> str:
        def mark(ok: bool) -> str:
            return "HIT " if ok else "MISS"
        return (
            f"  {self.player:<22} retain {mark(self.retain_hit)}  "
            f"route {mark(self.route_hit)} "
            f"({self.route_sim or '-'} vs {self.route_actual})  "
            f"terms {mark(self.within_max)} "
            f"(${self.salary_actual:,} vs max ${self.max_sim:,})"
        )


def score_moves(planned_ids: set[str], state, agents, env) -> list[MoveScore]:
    """Per-move hits and misses against the real Golden State offseason."""
    from mironba.rules.signing import TeamCapState

    salaries = actual_salaries()
    by_id = {a.player_id: a for a in agents}
    scores: list[MoveScore] = []
    committed = state.committed_salary
    roster = state.roster_count

    for pid, route in ACTUAL_ROUTES.items():
        agent = by_id.get(pid)
        salary = salaries.get(pid)
        if agent is None or salary is None:
            continue
        check = check_signing(
            TeamCapState(state.team_id, state.season,
                         committed_salary=committed, roster_count=roster),
            agent, salary, env, expected_route=route,
        )
        scores.append(
            MoveScore(
                player=agent.name,
                player_id=pid,
                retained_actual=True,
                retained_sim=pid in planned_ids,
                route_actual=route,
                route_sim=check.matched_route,
                salary_actual=salary,
                max_sim=check.max_first_year,
                within_max=check.within_maximum,
            )
        )
        committed += salary
        roster += 1
    return scores
