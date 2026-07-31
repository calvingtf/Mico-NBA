"""Roster to projected wins, and the delta a trade would make.

Stage 2 and 3 of the v0 pipeline. Stage 1 (``models/value.py``) turns a
box-score season into a per-36 rate; this turns a set of those rates into a
win projection, and a change of roster into a change of projection.

## The forecast is a forecast

The distinction that decides whether any of this is honest: a player's
**quality** is estimated only from seasons strictly before the one being
predicted. Nothing in a projection for season S is fitted on season S.

Minute **allocation** is the harder question. Using season-S minutes would be
leakage of the worst kind — it encodes who got injured, who was benched, and
who broke out, which is most of what makes a season surprising. So allocation
also comes from prior seasons, renormalised over the season-S roster. The cost
is real: a team that gives a breakout rookie 30 minutes a night will be
mispriced, and so will a team whose star misses fifty games.

## Why a delta needs intervals and a win total does not

A projected win total can be checked: the season happens. A *counterfactual*
trade delta cannot. There is no world where the Lakers both did and did not
trade for a player, so the delta has no observable ground truth at any point,
ever — not at M4, not later. What can be bounded is how much of the delta is
model error, and that is what ``WinDelta`` reports: the point estimate is the
difference of two projections, and the interval is inherited from the residual
spread of those projections on held-out seasons. Reporting a delta as a single
number would imply a precision the construction cannot have.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from mironba.models.value import (
    EXCLUDED_SEASONS,
    PlayerSeason,
    TeamSeason,
    ValueModel,
)

#: Minutes per team-game available to distribute. Five players, 48 minutes.
TEAM_MINUTES_PER_GAME = 240.0

#: A player with no prior season gets replacement level and a bench-sized
#: minute share. Rookies are genuinely unknown to this model — it has no
#: college or draft input — and pretending otherwise would be invention.
ROOKIE_MINUTES_PER_GAME = 12.0


@dataclass
class TeamProjection:
    season: str
    team: str
    strength: float
    projected_wins: float
    known_share: float
    players_priced: int
    players_replacement: int


@dataclass
class WinModel:
    """Linear map from team strength to wins, plus its residual spread."""

    slope: float
    intercept: float
    residual_sd: float
    fitted_on: tuple[str, ...] = ()
    n_teams: int = 0

    def wins(self, strength: float) -> float:
        return self.slope * strength + self.intercept

    def describe(self) -> str:
        return (
            f"wins = {self.slope:.2f} x strength + {self.intercept:.1f}   "
            f"(fit on {self.n_teams} team-seasons, residual sd "
            f"{self.residual_sd:.2f} wins)"
        )


def prior_seasons(season: str, all_seasons: list[str]) -> list[str]:
    """Every usable season strictly before ``season``."""
    return [
        s for s in sorted(all_seasons)
        if s < season and s not in EXCLUDED_SEASONS
    ]


def player_quality(
    model: ValueModel,
    players: list[PlayerSeason],
    season: str,
    all_seasons: list[str],
    *,
    half_life: float = 1.0,
) -> tuple[dict[str, float], dict[str, float]]:
    """Per-player quality and minutes-per-game, from seasons before ``season``.

    Recent seasons count for more, with an exponential weight and a one-season
    half-life. Also weighted by minutes played, so a full season outweighs a
    fragment. Both weights multiply.
    """
    usable = prior_seasons(season, all_seasons)
    if not usable:
        return {}, {}
    latest = max(usable)
    latest_index = sorted(all_seasons).index(latest)
    order = {s: i for i, s in enumerate(sorted(all_seasons))}

    quality_num: dict[str, float] = defaultdict(float)
    quality_den: dict[str, float] = defaultdict(float)
    minutes_num: dict[str, float] = defaultdict(float)
    minutes_den: dict[str, float] = defaultdict(float)

    for player in players:
        if player.season not in usable:
            continue
        age = latest_index - order[player.season]
        recency = 0.5 ** (age / half_life)
        weight = recency * player.minutes
        quality_num[player.player_id] += weight * model.box_pm36(player)
        quality_den[player.player_id] += weight
        per_game = player.minutes / max(player.games, 1)
        minutes_num[player.player_id] += recency * per_game
        minutes_den[player.player_id] += recency

    quality = {
        pid: quality_num[pid] / quality_den[pid]
        for pid in quality_num if quality_den[pid] > 0
    }
    minutes = {
        pid: minutes_num[pid] / minutes_den[pid]
        for pid in minutes_num if minutes_den[pid] > 0
    }
    return quality, minutes


def team_strength(
    roster: list[str],
    quality: dict[str, float],
    minutes: dict[str, float],
    replacement: float,
) -> tuple[float, float, int, int]:
    """Minutes-share-weighted mean quality over a roster.

    Shares rather than totals, for two reasons. It makes strength a rate, so
    teams are comparable. And it absorbs a quirk of the source: the stats
    endpoint attributes a traded player's whole season to his final team, which
    inflates a team's summed minutes by about 4%. Normalising removes it.
    """
    weights, values, priced, filled = [], [], 0, 0
    for pid in roster:
        per_game = minutes.get(pid)
        if per_game is None:
            per_game = ROOKIE_MINUTES_PER_GAME
        if pid in quality:
            values.append(quality[pid])
            priced += 1
        else:
            values.append(replacement)
            filled += 1
        weights.append(per_game)

    if not weights or sum(weights) <= 0:
        return replacement, 0.0, priced, filled

    w = np.array(weights, dtype=float)
    v = np.array(values, dtype=float)
    # Cap total minutes at what a team actually has to give out. Prior-season
    # minutes over a 20-man roster routinely exceed 240 a game; renormalising
    # is what turns "who is here" into "who plays".
    share = w / w.sum()
    known = float(share[[pid in quality for pid in roster]].sum()) if roster else 0.0
    return float((share * v).sum()), known, priced, filled


def center_by_season(
    strengths: dict[tuple[str, str], float]
) -> dict[tuple[str, str], float]:
    """Express each team's strength relative to its own season's league mean.

    Not a tweak — a correction for a real bias that showed up as a +7.8 win
    systematic over-prediction on held-out data.

    The v0 metric is not era-neutral. Its largest fitted weight is on made
    three-pointers, and three-point volume grew steadily across the seasons
    ingested, so league-mean strength drifted from -0.72 in 2015-16 to +0.73 in
    2023-24 with no change in how good the league was. A win model fitted on
    pooled seasons reads that drift as teams getting better and projects the
    held-out season too high.

    Wins are zero-sum: thirty teams share 1,230 of them and the league mean is
    41 every year, by construction. So the only part of strength that can
    predict wins is the part that varies *within* a season. Centering discards
    the rest, and it does so without needing to know what the era trend was.
    """
    by_season: dict[str, list[float]] = {}
    for (season, _), value in strengths.items():
        by_season.setdefault(season, []).append(value)
    means = {s: float(np.mean(v)) for s, v in by_season.items()}
    return {key: value - means[key[0]] for key, value in strengths.items()}


def fit_win_model(
    strengths: dict[tuple[str, str], float],
    teams: list[TeamSeason],
    seasons: tuple[str, ...],
) -> WinModel:
    """Least squares from season-centered team strength to wins."""
    x, y = [], []
    for team in teams:
        if team.season not in seasons or team.season in EXCLUDED_SEASONS:
            continue
        key = (team.season, team.team_id)
        if key not in strengths:
            continue
        x.append(strengths[key])
        # Normalised to 82 games so short seasons, if ever included, do not
        # drag the intercept.
        y.append(team.wins * 82.0 / max(team.games, 1))
    if len(x) < 10:
        raise ValueError(f"only {len(x)} team-seasons to fit the win model")

    x_arr, y_arr = np.array(x), np.array(y)
    slope, intercept = np.polyfit(x_arr, y_arr, 1)
    residuals = y_arr - (slope * x_arr + intercept)
    return WinModel(
        slope=float(slope),
        intercept=float(intercept),
        residual_sd=float(residuals.std(ddof=2)),
        fitted_on=seasons,
        n_teams=len(x),
    )


@dataclass
class WinDelta:
    """The change a roster change makes, with an interval, never a point.

    ``low`` and ``high`` are not a confidence interval in the strict sense.
    They are the point estimate widened by the win model's own residual spread
    on held-out data, propagated across the two projections being differenced.
    That is the smallest honest statement available: the delta cannot be less
    uncertain than the projections it is a difference of.
    """

    before: float
    after: float
    residual_sd: float

    @property
    def point(self) -> float:
        return self.after - self.before

    @property
    def sd(self) -> float:
        # Two projections, same model, so their errors are correlated rather
        # than independent — differencing cancels the shared part. sqrt(2) is
        # the independent case and is therefore an upper bound on the spread.
        return self.residual_sd * float(np.sqrt(2))

    def interval(self, z: float = 1.0) -> tuple[float, float]:
        return (self.point - z * self.sd, self.point + z * self.sd)

    def describe(self) -> str:
        low, high = self.interval()
        return (
            f"{self.point:+.1f} wins  [{low:+.1f}, {high:+.1f}] at 1 sd  "
            f"({self.before:.1f} -> {self.after:.1f})"
        )


def win_delta(
    roster_before: list[str],
    roster_after: list[str],
    quality: dict[str, float],
    minutes: dict[str, float],
    model: WinModel,
    replacement: float,
) -> WinDelta:
    """Projected win change from swapping one roster for another."""
    before, *_ = team_strength(roster_before, quality, minutes, replacement)
    after, *_ = team_strength(roster_after, quality, minutes, replacement)
    return WinDelta(
        before=model.wins(before),
        after=model.wins(after),
        residual_sd=model.residual_sd,
    )
