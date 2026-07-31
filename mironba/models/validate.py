"""Out-of-sample validation of the v0 value model, against baselines.

    python -m mironba.models.validate

The only question that matters here: does a roster-composition model beat
knowing nothing except last year's record? Two baselines, both of which are
harder to beat than they look:

  **previous-season wins** — last year's win total, unchanged. Team quality is
  strongly autocorrelated, so this is genuinely competitive.

  **previous-season wins regressed to .500** — the same, shrunk toward 41 by a
  factor fitted on the training seasons rather than assumed. Regression to the
  mean is real and this baseline captures it, which usually makes it the
  stronger of the two.

A model that cannot beat both is not adding anything a lookup table does not
already have, and the honest response is to say so.

Everything is fit on training seasons and evaluated on a season the fit never
saw — including the ridge penalty, the win-model coefficients, and the shrink
factor. The one thing that is *not* out of sample is roster membership: to
project 2023-24 we have to know who was on each team in 2023-24. That is the
point of a trade simulator, and it is not leakage of the outcome; the leakage
that would matter, minutes and quality, both come from prior seasons only.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

from mironba.models.value import (
    EXCLUDED_SEASONS,
    fit_value_model,
    load_player_seasons,
    load_team_seasons,
)
from mironba.models.win_delta import (
    center_by_season,
    fit_win_model,
    player_quality,
    prior_seasons,
    team_strength,
)


@dataclass
class Scored:
    name: str
    mae: float
    rmse: float
    n: int
    bias: float

    def line(self) -> str:
        return (
            f"  {self.name:38} MAE {self.mae:5.2f}   RMSE {self.rmse:5.2f}   "
            f"bias {self.bias:+5.2f}   n={self.n}"
        )


def _score(name: str, predicted: list[float], actual: list[float]) -> Scored:
    p, a = np.array(predicted), np.array(actual)
    return Scored(
        name=name,
        mae=float(np.abs(p - a).mean()),
        rmse=float(np.sqrt(((p - a) ** 2).mean())),
        n=len(p),
        bias=float((p - a).mean()),
    )


def fit_shrink(teams, train_seasons: tuple[str, ...]) -> float:
    """How far last year's wins should be pulled toward .500, from data.

    Fitted rather than assumed. A hardcoded 0.75 would make the baseline a
    choice of mine rather than a property of the league, and a baseline you
    tuned is not a baseline.
    """
    by_team = {(t.season, t.team_id): t for t in teams}
    x, y = [], []
    for team in teams:
        if team.season not in train_seasons or team.season in EXCLUDED_SEASONS:
            continue
        earlier = [s for s in train_seasons if s < team.season]
        if not earlier:
            continue
        previous = by_team.get((max(earlier), team.team_id))
        if previous is None or previous.season in EXCLUDED_SEASONS:
            continue
        x.append(previous.wins * 82.0 / max(previous.games, 1) - 41.0)
        y.append(team.wins * 82.0 / max(team.games, 1) - 41.0)
    if len(x) < 10:
        return 1.0
    slope = float(np.polyfit(np.array(x), np.array(y), 1)[0])
    return slope


def validate(test_season: str, *, quiet: bool = False, centered: bool = True) -> dict:
    players = load_player_seasons()
    teams = load_team_seasons()
    all_seasons = sorted({p.season for p in players})

    train = tuple(prior_seasons(test_season, all_seasons))
    if not train:
        raise ValueError(f"no training seasons before {test_season}")

    say = (lambda *a: None) if quiet else print
    say(f"\n{'=' * 72}\n  held-out season: {test_season}")
    say(f"  trained on:      {', '.join(train)}")
    say(f"  excluded:        {', '.join(EXCLUDED_SEASONS)}\n{'=' * 72}")

    # Stage 1, on training seasons only.
    value_model = fit_value_model(players, train)
    say("\nstage 1  player rate")
    say("  " + value_model.describe().replace("\n", "\n  "))

    # Stage 2 + 3: strength for every training team-season, then wins.
    rosters: dict[tuple[str, str], list[str]] = {}
    team_ids: dict[tuple[str, str], str] = {}
    for team in teams:
        rosters.setdefault((team.season, team.team_id), [])
    id_by_abbr = _team_id_by_abbreviation(players, teams)
    for player in players:
        key = (player.season, id_by_abbr.get((player.season, player.team)))
        if key in rosters:
            rosters[key].append(player.player_id)

    strengths: dict[tuple[str, str], float] = {}
    for season in train:
        quality, minutes = player_quality(value_model, players, season, all_seasons)
        if not quality:
            continue
        for team in teams:
            if team.season != season:
                continue
            key = (season, team.team_id)
            strength, *_ = team_strength(
                rosters.get(key, []), quality, minutes, value_model.replacement_pm36
            )
            strengths[key] = strength

    # Centered within season, because wins are zero-sum and the metric is not
    # era-neutral. See center_by_season for the +7.8 win bias this removes.
    # `centered=False` reproduces the pre-fix model, which exists so the
    # zero-sum invariant can be shown failing on it.
    raw_train = dict(strengths)
    if centered:
        strengths = center_by_season(strengths)
    win_model = fit_win_model(strengths, teams, train)
    say("\nstage 3  wins")
    say(f"  {win_model.describe()}")

    # Predict the held-out season.
    quality, minutes = player_quality(value_model, players, test_season, all_seasons)
    by_team = {(t.season, t.team_id): t for t in teams}

    # The held-out season is centered against its own league mean, computed
    # from its own thirty teams. That uses no outcome — only the rosters we
    # already know — and it is the same transform the fit was trained under.
    raw_test = {}
    for team in teams:
        if team.season != test_season:
            continue
        key = (test_season, team.team_id)
        raw_test[key] = team_strength(
            rosters.get(key, []), quality, minutes, value_model.replacement_pm36
        )[0]
    centered_test = center_by_season(raw_test) if centered else raw_test
    previous_season = max(s for s in all_seasons if s < test_season)

    shrink = fit_shrink(teams, train)
    predicted_model, predicted_prev, predicted_shrunk, actual = [], [], [], []
    coverage = []
    for team in teams:
        if team.season != test_season:
            continue
        key = (test_season, team.team_id)
        _, known, priced, filled = team_strength(
            rosters.get(key, []), quality, minutes, value_model.replacement_pm36
        )
        strength = centered_test[key]
        previous = by_team.get((previous_season, team.team_id))
        if previous is None:
            continue
        actual.append(team.wins * 82.0 / max(team.games, 1))
        predicted_model.append(win_model.wins(strength))
        prev_wins = previous.wins * 82.0 / max(previous.games, 1)
        predicted_prev.append(prev_wins)
        predicted_shrunk.append(41.0 + shrink * (prev_wins - 41.0))
        coverage.append((known, priced, filled))

    scores = [
        _score("v0 roster model", predicted_model, actual),
        _score("baseline: previous-season wins", predicted_prev, actual),
        # Stable key. The shrink factor is refit per held-out season, so
        # putting it in the name made the dict key move between seasons and
        # broke any attempt to pool results across them.
        _score("baseline: previous regressed to .500", predicted_shrunk, actual),
    ]

    say(f"\nheld-out {test_season}, {len(actual)} teams")
    for score in scores:
        say(score.line())

    beats_all = all(scores[0].mae < s.mae for s in scores[1:])
    say("")
    if beats_all:
        say(f"  v0 BEATS both baselines on MAE.")
    else:
        worst = min(scores[1:], key=lambda s: s.mae)
        say(f"  v0 DOES NOT beat all baselines. Best baseline is "
            f"'{worst.name}' at MAE {worst.mae:.2f} against v0's {scores[0].mae:.2f}.")

    from mironba.models.diagnostics import zero_sum_balance, worst_season_error

    balances = zero_sum_balance(strengths, teams, win_model, train)
    mean_known = float(np.mean([c[0] for c in coverage])) if coverage else 0.0
    say(f"\n  minute share priced from prior seasons: {mean_known:.1%}")
    say(f"  players priced / replacement: "
        f"{sum(c[1] for c in coverage)} / {sum(c[2] for c in coverage)}")

    return {
        "test_season": test_season,
        "centered": centered,
        "zero_sum_worst_per_team": worst_season_error(balances),
        "zero_sum_balances": [
            {"season": b.season, "mean_strength": b.mean_strength,
             "predicted": b.predicted_wins, "actual": b.actual_wins,
             "error_per_team": b.error_per_team}
            for b in balances
        ],
        "errors": {
            "v0 roster model": [abs(p - a) for p, a in zip(predicted_model, actual)],
            "baseline: previous-season wins":
                [abs(p - a) for p, a in zip(predicted_prev, actual)],
            "baseline: previous regressed to .500":
                [abs(p - a) for p, a in zip(predicted_shrunk, actual)],
        },
        "train_seasons": list(train),
        "shrink": shrink,
        "scores": {s.name: {"mae": s.mae, "rmse": s.rmse, "bias": s.bias} for s in scores},
        "beats_all_baselines": beats_all,
        "mean_known_minute_share": mean_known,
        "win_model": {
            "slope": win_model.slope,
            "intercept": win_model.intercept,
            "residual_sd": win_model.residual_sd,
        },
        "value_model_alpha": value_model.alpha,
        "replacement_pm36": value_model.replacement_pm36,
    }


def _team_id_by_abbreviation(players, teams) -> dict[tuple[str, str], str]:
    """Map (season, abbreviation) -> team id.

    The player endpoint gives abbreviations and the team endpoint gives ids and
    full names, with no shared key. Built by matching the 30 abbreviations of a
    season against the 30 team ids, using the fact that both are stable within
    a season and that team ids never change.
    """
    # Team ids are constant across seasons, so one pass over any season that
    # has both is enough to learn the mapping, and it is then reused.
    abbr_to_id: dict[str, str] = {}
    known = {
        "ATL": "1610612737", "BOS": "1610612738", "BKN": "1610612751",
        "CHA": "1610612766", "CHI": "1610612741", "CLE": "1610612739",
        "DAL": "1610612742", "DEN": "1610612743", "DET": "1610612765",
        "GSW": "1610612744", "HOU": "1610612745", "IND": "1610612754",
        "LAC": "1610612746", "LAL": "1610612747", "MEM": "1610612763",
        "MIA": "1610612748", "MIL": "1610612749", "MIN": "1610612750",
        "NOP": "1610612740", "NYK": "1610612752", "OKC": "1610612760",
        "ORL": "1610612753", "PHI": "1610612755", "PHX": "1610612756",
        "POR": "1610612757", "SAC": "1610612758", "SAS": "1610612759",
        "TOR": "1610612761", "UTA": "1610612762", "WAS": "1610612764",
    }
    abbr_to_id.update(known)
    return {
        (season, abbr): team_id
        for season in {p.season for p in players}
        for abbr, team_id in abbr_to_id.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the v0 value model.")
    parser.add_argument("--seasons", nargs="+", default=["2023-24", "2024-25"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = [validate(s, quiet=args.json) for s in args.seasons]
    if args.json:
        import json

        print(json.dumps(results, indent=2))
        return 0

    print("\n" + "=" * 72)
    print("  summary")
    print("=" * 72)
    for result in results:
        verdict = "beats both" if result["beats_all_baselines"] else "DOES NOT beat both"
        model = result["scores"]["v0 roster model"]["mae"]
        best = min(v["mae"] for k, v in result["scores"].items() if k != "v0 roster model")
        print(f"  {result['test_season']}   v0 MAE {model:5.2f}   "
              f"best baseline {best:5.2f}   -> {verdict}")
    return 0 if all(r["beats_all_baselines"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
