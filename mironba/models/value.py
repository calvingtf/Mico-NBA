"""Player value, v0. Deliberately simple, and every choice stated.

The charter's M2 is a hierarchical Bayesian projection. This is not that, on
purpose: a hierarchical model that has never been checked against a baseline is
an expensive way to be wrong. This version is a two-stage linear fit that can
be read in one sitting, refit in a second, and beaten on purpose later.

The pipeline, in three steps, each one fit on training seasons only:

  1. **Player rate.** Ridge regression from per-minute box-score rates to the
     player's per-minute plus/minus. Output is a per-36-minute number,
     ``box_pm36``.
  2. **Team strength.** Minutes-share-weighted mean of ``box_pm36`` over a
     roster. Shares, not totals, so it is a rate and comparable across teams.
  3. **Wins.** Linear map from team strength to regular-season wins.

## Why plus/minus as the target, given it is a bad metric

Raw plus/minus is heavily team-dependent — a fifth option on a good team
outscores a first option on a bad one — and it would be indefensible as a value
metric on its own. It is used here as a *regression target*, not as an output.
What comes out is the part of plus/minus that box-score production explains,
which is exactly the part that travels with a player to another team. The
team-context noise is what the regression discards.

This is the same idea as Box Plus/Minus, arrived at with less care. The
difference from BPM proper is that BPM regresses against *adjusted* plus/minus
and includes team-level adjustment terms; this does neither, so it will inherit
some team quality. Stated as a known bias rather than corrected, because
correcting it is the hierarchical model's job.

## Why ridge rather than ordinary least squares

Box-score rates are strongly collinear — FGA with PTS, REB with DREB — and OLS
on collinear features produces large offsetting coefficients that swing between
seasons. Ridge trades a little bias for coefficients that are stable enough to
be worth reading. The penalty is chosen once, by cross-validation on the
training seasons, and recorded.

## What is excluded and why

* **2019-20 and 2020-21.** One season was suspended and resumed in a bubble;
  the other was 72 games with no crowds. Both have wins that do not mean the
  same thing. Excluded in the model rather than in the ingest so the choice is
  visible and reversible.
* **Playoffs.** The target is regular-season wins.
* **Players under a minutes floor.** A player with 40 minutes has a per-minute
  plus/minus dominated by noise, and including them lets garbage-time samples
  set coefficients. They are excluded from *fitting* and assigned replacement
  level when *predicting*, which is the honest treatment: we do not know they
  are bad, we know we cannot tell.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "nba-stats"

#: Seasons whose win totals are not comparable to a normal 82-game year.
EXCLUDED_SEASONS = ("2019-20", "2020-21")

#: Minutes below which a per-minute rate is noise rather than signal. 500
#: minutes is roughly 10 minutes a night for half a season.
MIN_MINUTES_TO_FIT = 500.0

#: Box-score counting stats used as features. Deliberately raw counts converted
#: to per-minute rates rather than efficiency ratios: a ratio hides volume, and
#: volume is most of what separates a starter from a bench player.
FEATURES = (
    "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
    "OREB", "DREB", "AST", "TOV", "STL", "BLK", "BLKA", "PF", "PFD", "PTS",
)

#: A player we cannot evaluate is assumed to be a marginal NBA player, not an
#: average one. Expressed in the same per-36 units as ``box_pm36`` and taken
#: from the fitted distribution rather than asserted — see ``replacement_level``.
REPLACEMENT_PERCENTILE = 20.0


@dataclass
class PlayerSeason:
    season: str
    player_id: str
    name: str
    team: str
    games: int
    minutes: float
    plus_minus: float
    counts: dict[str, float]

    def rates(self) -> np.ndarray:
        """Per-minute box-score rates."""
        return np.array([self.counts[f] / self.minutes for f in FEATURES])

    @property
    def pm_per_minute(self) -> float:
        return self.plus_minus / self.minutes


@dataclass
class TeamSeason:
    season: str
    team_id: str
    name: str
    wins: int
    games: int
    point_differential: float


def load_player_seasons(root: Path = SNAPSHOT) -> list[PlayerSeason]:
    rows = []
    with (root / "player_seasons.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            minutes = float(row["MIN"] or 0)
            if minutes <= 0:
                continue
            rows.append(
                PlayerSeason(
                    season=row["season"],
                    player_id=row["PLAYER_ID"],
                    name=row["PLAYER_NAME"],
                    team=row["TEAM_ABBREVIATION"],
                    games=int(row["GP"]),
                    minutes=minutes,
                    plus_minus=float(row["PLUS_MINUS"] or 0),
                    counts={f: float(row[f] or 0) for f in FEATURES},
                )
            )
    return rows


def load_team_seasons(root: Path = SNAPSHOT) -> list[TeamSeason]:
    rows = []
    with (root / "team_seasons.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                TeamSeason(
                    season=row["season"],
                    team_id=row["TEAM_ID"],
                    name=row["TEAM_NAME"],
                    wins=int(row["W"]),
                    games=int(row["GP"]),
                    point_differential=float(row["PLUS_MINUS"] or 0),
                )
            )
    return rows


# --------------------------------------------------------------------------
# Stage 1: player rate
# --------------------------------------------------------------------------


@dataclass
class ValueModel:
    """Fitted box-score-to-plus/minus weights, in per-36 units."""

    coefficients: np.ndarray
    intercept: float
    feature_names: tuple[str, ...]
    alpha: float
    mean: np.ndarray
    scale: np.ndarray
    replacement_pm36: float
    fitted_on: tuple[str, ...] = ()
    n_players: int = 0

    def box_pm36(self, player: PlayerSeason) -> float:
        """Box-score-predicted plus/minus per 36 minutes."""
        z = (player.rates() - self.mean) / self.scale
        return float((z @ self.coefficients + self.intercept) * 36.0)

    def describe(self) -> str:
        order = np.argsort(-np.abs(self.coefficients))
        top = ", ".join(
            f"{self.feature_names[i]} {self.coefficients[i]:+.3f}" for i in order[:6]
        )
        return (
            f"ridge(alpha={self.alpha:g}) on {self.n_players} player-seasons "
            f"from {len(self.fitted_on)} seasons\n"
            f"  largest standardised weights: {top}\n"
            f"  replacement level: {self.replacement_pm36:+.2f} per 36"
        )


def fit_value_model(
    players: list[PlayerSeason],
    seasons: tuple[str, ...],
    *,
    alphas: tuple[float, ...] = (0.1, 1.0, 10.0, 100.0, 1000.0),
    folds: int = 5,
) -> ValueModel:
    """Ridge from per-minute box rates to per-minute plus/minus.

    Weighted by minutes: a 2,500-minute season is a better estimate of a rate
    than a 500-minute one, and unweighted least squares would treat them as
    equally informative.
    """
    sample = [
        p for p in players
        if p.season in seasons and p.minutes >= MIN_MINUTES_TO_FIT
    ]
    if not sample:
        raise ValueError(f"no player-seasons to fit on for {seasons}")

    X = np.vstack([p.rates() for p in sample])
    y = np.array([p.pm_per_minute for p in sample])
    w = np.array([p.minutes for p in sample])

    mean, scale = X.mean(axis=0), X.std(axis=0)
    scale[scale == 0] = 1.0
    Z = (X - mean) / scale

    alpha = _choose_alpha(Z, y, w, alphas, folds)
    coefficients, intercept = _weighted_ridge(Z, y, w, alpha)

    model = ValueModel(
        coefficients=coefficients,
        intercept=intercept,
        feature_names=FEATURES,
        alpha=alpha,
        mean=mean,
        scale=scale,
        replacement_pm36=0.0,
        fitted_on=seasons,
        n_players=len(sample),
    )
    # Replacement level is read off the fitted distribution rather than
    # asserted, so it moves with the model instead of becoming a stale constant.
    values = np.array([model.box_pm36(p) for p in sample])
    model.replacement_pm36 = float(np.percentile(values, REPLACEMENT_PERCENTILE))
    return model


def _weighted_ridge(
    Z: np.ndarray, y: np.ndarray, w: np.ndarray, alpha: float
) -> tuple[np.ndarray, float]:
    """Closed-form weighted ridge. The intercept is not penalised."""
    sw = np.sqrt(w)
    y_mean = float(np.average(y, weights=w))
    Zw = Z * sw[:, None]
    yw = (y - y_mean) * sw
    gram = Zw.T @ Zw + alpha * np.eye(Z.shape[1])
    coefficients = np.linalg.solve(gram, Zw.T @ yw)
    return coefficients, y_mean


def _choose_alpha(
    Z: np.ndarray, y: np.ndarray, w: np.ndarray,
    alphas: tuple[float, ...], folds: int,
) -> float:
    """K-fold on the training set only. Never touches a held-out season."""
    rng = np.random.default_rng(20260731)
    order = rng.permutation(len(y))
    chunks = np.array_split(order, folds)
    best, best_error = alphas[0], np.inf
    for alpha in alphas:
        errors = []
        for i in range(folds):
            test = chunks[i]
            train = np.concatenate([chunks[j] for j in range(folds) if j != i])
            coefficients, intercept = _weighted_ridge(
                Z[train], y[train], w[train], alpha
            )
            predicted = Z[test] @ coefficients + intercept
            errors.append(np.average((predicted - y[test]) ** 2, weights=w[test]))
        mean_error = float(np.mean(errors))
        if mean_error < best_error:
            best, best_error = alpha, mean_error
    return best


def replacement_level(model: ValueModel) -> float:
    return model.replacement_pm36
