"""Checks that decide whether a modelling change was a fix or a rescue.

Two questions M2 left open, both answerable without touching a held-out season.

**Was the +7.8 win bias visible in-sample?** If it was, centring was a
correction to a defect the training data already showed, and the fact that it
also improved held-out MAE is a consequence rather than the motivation. If it
was not, then the change was selected by looking at the test set and the honest
description is a rescue. ``zero_sum_balance`` answers it with an invariant that
needs no baseline and no held-out data: thirty teams share exactly 1,230 wins
every season, so a win model must reproduce that total for each *training*
season it was fitted on.

The subtlety is why a pooled least-squares fit does not get this for free.
Fitting with an intercept forces residuals to sum to zero **overall**, so the
uncentered model balances across all seasons pooled and still misses badly
season by season — too low early, too high late — because league-mean strength
drifts with the era. Checking per season is what exposes it.

**Does the pooled MAE advantage clear noise?** ``paired_comparison`` compares
per-team absolute errors as matched pairs, which is the right test: the same
thirty teams are scored by every method, so the season-to-season difficulty
that dominates the raw numbers cancels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mironba.models.value import EXCLUDED_SEASONS


@dataclass(frozen=True, slots=True)
class SeasonBalance:
    """Whether one season's predictions add up to the wins that exist."""

    season: str
    mean_strength: float
    predicted_wins: float
    actual_wins: float
    teams: int

    @property
    def error(self) -> float:
        return self.predicted_wins - self.actual_wins

    @property
    def error_per_team(self) -> float:
        return self.error / self.teams if self.teams else 0.0

    def line(self) -> str:
        return (
            f"  {self.season:9} mean strength {self.mean_strength:+7.3f}   "
            f"predicted {self.predicted_wins:7.1f}   actual {self.actual_wins:7.1f}   "
            f"error {self.error:+7.1f} ({self.error_per_team:+.2f}/team)"
        )


def zero_sum_balance(
    strengths: dict[tuple[str, str], float],
    teams: list,
    win_model,
    seasons: tuple[str, ...],
) -> list[SeasonBalance]:
    """Per-season totals of predicted against actual wins, on training data.

    An invariant, not a fit statistic. The league plays a fixed number of games
    and every one produces exactly one win, so the thirty predictions for a
    season have a known sum whatever the model believes about any team. A model
    that misses it is wrong in a way that has nothing to do with skill at
    ranking teams.
    """
    by_season: dict[str, list[tuple[float, float]]] = {}
    for team in teams:
        if team.season not in seasons or team.season in EXCLUDED_SEASONS:
            continue
        key = (team.season, team.team_id)
        if key not in strengths:
            continue
        predicted = win_model.wins(strengths[key])
        actual = team.wins * 82.0 / max(team.games, 1)
        by_season.setdefault(team.season, []).append(
            (strengths[key], predicted, actual)  # type: ignore[arg-type]
        )

    out = []
    for season in sorted(by_season):
        rows = by_season[season]
        out.append(
            SeasonBalance(
                season=season,
                mean_strength=float(np.mean([r[0] for r in rows])),
                predicted_wins=float(np.sum([r[1] for r in rows])),
                actual_wins=float(np.sum([r[2] for r in rows])),
                teams=len(rows),
            )
        )
    return out


def worst_season_error(balances: list[SeasonBalance]) -> float:
    """Largest per-team miss across seasons. The headline number."""
    return max((abs(b.error_per_team) for b in balances), default=0.0)


@dataclass(frozen=True, slots=True)
class PairedResult:
    """v0 against one baseline, on matched per-team absolute errors."""

    name: str
    mean_difference: float
    sd_difference: float
    n: int
    t_statistic: float
    p_value: float
    wins: int
    losses: int

    @property
    def separated(self) -> bool:
        """Whether the difference clears noise at the conventional threshold."""
        return self.p_value < 0.05

    def line(self) -> str:
        verdict = "SEPARATED" if self.separated else "within noise"
        return (
            f"  vs {self.name:34} mean diff {self.mean_difference:+6.2f} wins   "
            f"t={self.t_statistic:+5.2f}  p={self.p_value:.3f}   "
            f"{self.wins}W/{self.losses}L   {verdict}"
        )


def paired_comparison(
    name: str, model_errors: list[float], baseline_errors: list[float]
) -> PairedResult:
    """Paired t-test on absolute errors. Negative mean difference favours v0.

    Paired rather than two-sample because every team is scored by both methods
    on the same season. Unpaired testing would treat "2022-23 was an easier
    season to predict" as noise in the comparison, when it is shared by both
    and cancels exactly.
    """
    from scipy import stats

    model = np.asarray(model_errors, dtype=float)
    baseline = np.asarray(baseline_errors, dtype=float)
    if model.shape != baseline.shape:
        raise ValueError("paired comparison needs matched arrays")

    difference = model - baseline
    spread = float(difference.std(ddof=1)) if len(difference) > 1 else 0.0
    if spread == 0.0:
        # Every pair differs by the same amount, so there is no variance for a
        # t-test to divide by and scipy warns about catastrophic cancellation.
        # Degenerate but not impossible — two methods can agree exactly on a
        # small fixture — and the answer is unambiguous rather than uncertain:
        # a constant non-zero difference is a certain separation, and a
        # constant zero difference is certainly no difference.
        constant = float(difference.mean())
        return PairedResult(
            name=name,
            mean_difference=constant,
            sd_difference=0.0,
            n=len(difference),
            t_statistic=float("-inf") if constant < 0 else
            (float("inf") if constant > 0 else 0.0),
            p_value=0.0 if constant != 0 else 1.0,
            wins=int((difference < 0).sum()),
            losses=int((difference > 0).sum()),
        )
    statistic, p_value = stats.ttest_rel(model, baseline)
    return PairedResult(
        name=name,
        mean_difference=float(difference.mean()),
        sd_difference=float(difference.std(ddof=1)),
        n=len(difference),
        t_statistic=float(statistic),
        p_value=float(p_value),
        wins=int((difference < 0).sum()),
        losses=int((difference > 0).sum()),
    )


def leave_one_season_out(
    per_season: dict[str, tuple[list[float], list[float]]], name: str
) -> list[tuple[str, float]]:
    """Pooled mean difference with each season dropped in turn.

    A pooled advantage carried by one season is a different claim from one that
    holds across seasons, and the pooled number alone cannot tell them apart.
    """
    seasons = sorted(per_season)
    out = []
    for dropped in seasons:
        model, baseline = [], []
        for season in seasons:
            if season == dropped:
                continue
            m, b = per_season[season]
            model.extend(m)
            baseline.extend(b)
        difference = np.asarray(model) - np.asarray(baseline)
        out.append((dropped, float(difference.mean())))
    return out
