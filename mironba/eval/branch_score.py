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


#: Routes whose first-year figure is fully determined by the mechanism, so the
#: prediction can be checked exactly rather than against a ceiling.
#:
#: Only the minimum. Non-Bird was in this list and the data removed it:
#: Porzingis re-signed at $19,512,195 against a Non-Bird ceiling of $36,878,048,
#: which is 120% of his prior salary. So "120% of prior" is a *limit a team
#: negotiates under*, not a figure the rule dictates — Horford landing exactly
#: on his was him taking the maximum, not the mechanism forcing it.
#:
#: That distinction is the whole value of splitting exact from ceiling checks.
#: Reporting Non-Bird as an exact test would have scored two correct route
#: assignments as term failures.
#:
#: The minimum is genuinely determined, but only given the player's service
#: tier — and service is the one input in this branch that is recalled rather
#: than sourced. A minimum mismatch is therefore evidence about the service
#: figure, not about the solver, and is labelled that way.
EXACT_ROUTES = (MINIMUM,)


def exact_figure(route: str, agent, env) -> int | None:
    """The figure this route determines, or None if it only sets a ceiling."""
    from mironba.rules.cap import minimum_salary
    from mironba.rules.signing import NON_BIRD_RAISE_PCT

    if route == MINIMUM:
        try:
            return minimum_salary(env.season, agent.years_of_service)
        except KeyError:
            return None
    return None


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
    #: Set when the route determines an exact figure. None means the route only
    #: sets a ceiling and no tighter test is available.
    exact_expected: int | None = None

    @property
    def admits_exact_check(self) -> bool:
        return self.exact_expected is not None

    @property
    def exact_hit(self) -> bool | None:
        if self.exact_expected is None:
            return None
        return self.exact_expected == self.salary_actual

    @property
    def route_hit(self) -> bool:
        return self.route_sim == self.route_actual

    @property
    def retain_hit(self) -> bool:
        return self.retained_sim == self.retained_actual

    def line(self) -> str:
        def mark(ok: bool) -> str:
            return "HIT " if ok else "MISS"
        if self.exact_expected is None:
            terms = f"terms {mark(self.within_max)} (<= ${self.max_sim:,}, ceiling only)"
        elif self.exact_hit:
            terms = f"terms HIT  (EXACT: ${self.salary_actual:,})"
        else:
            terms = (
                f"terms MISS (EXACT: expected ${self.exact_expected:,}, "
                f"actual ${self.salary_actual:,} — service tier unsourced)"
            )
        return (
            f"  {self.player:<22} retain {mark(self.retain_hit)}  "
            f"route {mark(self.route_hit)} "
            f"({self.route_sim or '-'} vs {self.route_actual})  {terms}"
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
                exact_expected=exact_figure(route, agent, env),
            )
        )
        committed += salary
        roster += 1
    return scores


@dataclass
class Tally:
    """Recall, precision, and the proposal list that is their denominator."""

    proposed: list[str]
    actual: list[str]

    @property
    def hits(self) -> list[str]:
        return [p for p in self.proposed if p in set(self.actual)]

    @property
    def false_positives(self) -> list[str]:
        return [p for p in self.proposed if p not in set(self.actual)]

    @property
    def missed(self) -> list[str]:
        return [a for a in self.actual if a not in set(self.proposed)]

    @property
    def recall(self) -> float:
        return len(self.hits) / len(self.actual) if self.actual else 0.0

    @property
    def precision(self) -> float:
        return len(self.hits) / len(self.proposed) if self.proposed else 0.0

    def render(self, name=lambda pid: pid) -> str:
        lines = [
            f"  proposed {len(self.proposed)}, actual {len(self.actual)}, "
            f"overlap {len(self.hits)}",
            f"  recall    {self.recall:6.1%}  "
            f"({len(self.hits)}/{len(self.actual)} actual moves reproduced)",
            f"  precision {self.precision:6.1%}  "
            f"({len(self.hits)}/{len(self.proposed)} proposed moves happened)",
            "",
            "  full proposal list (the precision denominator):",
        ]
        for pid in self.proposed:
            mark = "happened" if pid in set(self.actual) else "DID NOT HAPPEN"
            lines.append(f"    {name(pid):<24} {mark}")
        if self.missed:
            lines.append("  actual moves the sim did not make:")
            for pid in self.missed:
                lines.append(f"    {name(pid):<24} MISSED")
        return "\n".join(lines)

