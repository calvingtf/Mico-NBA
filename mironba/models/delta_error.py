"""How wrong is a win delta? Measured, not assumed.

    python -m mironba.models.delta_error

M4 shipped two intervals for the same quantity and neither was measured:

  **12.05 wins** — differencing two whole-team projections, which assumes the
  two branches' errors are independent. They are not: same coach, same
  schedule, most of the same roster. Too wide.

  **2.00 wins** — propagating only the changed players' quality uncertainty,
  which assumes everything team-level cancels exactly. Fit, chemistry and
  coaching response do not. Too narrow.

They disagree about the only question anyone asks. At 12 nothing realistic
ranks; at 2 a three-win gap does. So this module stops arguing and measures.

**The design.** Every consecutive team-season pair in the performance ingest is
a natural experiment: a roster changed, and the win total changed with it.
Predict the second season's win delta from the first season's roster, the
changed players, and the model — then compare against what actually happened.
About 300 observations across nine usable seasons.

**What this measures is an upper bound, and the bound is loaded.** The actual
win change contains everything the model cannot see: injuries, player
development and decline, coaching changes, schedule strength, and luck. All of
it lands in the residual and none of it is the delta model's fault. So the
measured error is larger than the model's own error, by an unknown amount.

It is still worth more than either theoretical figure, because it is an upper
bound *derived from data* rather than a bound derived from an assumption about
which variance components cancel. A threshold set from it is conservative in
the direction of refusing to rank, which is the safe direction.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from mironba.models.value import (
    EXCLUDED_SEASONS,
    fit_value_model,
    load_player_seasons,
    load_team_seasons,
)
from mironba.models.win_delta import (
    DEFAULT_QUALITY_SD,
    ROOKIE_MINUTES_PER_GAME,
    center_by_season,
    fit_win_model,
    measure_quality_sd,
    player_quality,
    prior_seasons,
    team_strength,
)

#: Share of a team's minutes that must turn over for a transition to count as a
#: roster change worth predicting. Below this the delta is noise around zero and
#: the observation says nothing about the model.
MIN_TURNOVER = 0.05

#: A team-season where the top-minutes players missed an unusual number of
#: games. Used to split the sample, since injury is the largest confound and
#: the ingest can at least see games played.
INJURY_GAMES_LOST = 0.15


@dataclass
class Transition:
    """One team, one season to the next."""

    team_id: str
    from_season: str
    to_season: str
    predicted_delta: float
    actual_delta: float
    turnover: float
    #: Share of the *following* season's minutes taken by players who missed a
    #: large share of the schedule. A crude availability proxy — see the module
    #: docstring on what it can and cannot separate.
    disruption: float

    @property
    def error(self) -> float:
        return self.predicted_delta - self.actual_delta


def _rosters(players, teams) -> dict[tuple[str, str], list[str]]:
    ids = _team_ids()
    rosters: dict[tuple[str, str], list[str]] = {}
    for team in teams:
        rosters.setdefault((team.season, team.team_id), [])
    for player in players:
        key = (player.season, ids.get(player.team))
        if key in rosters:
            rosters[key].append(player.player_id)
    return rosters


def _team_ids() -> dict[str, str]:
    from mironba.models.validate import TEAM_ID_BY_ABBREVIATION

    return TEAM_ID_BY_ABBREVIATION


def _disruption(players, season: str, team_id: str, rosters) -> float:
    """Minute share held by players who appeared in under 60 of 82 games.

    The ingest publishes games played, not games available, so this cannot
    distinguish injury from rest from being benched. It is a proxy and is
    labelled as one; what it is good for is splitting the sample, not for
    correcting anything.
    """
    roster = set(rosters.get((season, team_id), []))
    if not roster:
        return 0.0
    total = missing = 0.0
    for player in players:
        if player.season != season or player.player_id not in roster:
            continue
        total += player.minutes
        if player.games < 60:
            missing += player.minutes
    return (missing / total) if total else 0.0


def measure(test_seasons: tuple[str, ...] | None = None) -> list[Transition]:
    """Predicted against actual win deltas, one row per team-season transition."""
    players = load_player_seasons()
    teams = load_team_seasons()
    all_seasons = sorted({p.season for p in players})
    usable = [s for s in all_seasons if s not in EXCLUDED_SEASONS]

    rosters = _rosters(players, teams)
    by_team = {(t.season, t.team_id): t for t in teams}
    out: list[Transition] = []

    for earlier, later in zip(usable, usable[1:]):
        # Everything is fitted on seasons strictly before the transition being
        # predicted, so no observation is scored by a model that saw it.
        train = tuple(s for s in prior_seasons(later, all_seasons))
        if len(train) < 3:
            continue
        value_model = fit_value_model(players, train)

        strengths: dict[tuple[str, str], float] = {}
        for season in train:
            quality, minutes = player_quality(
                value_model, players, season, all_seasons
            )
            if not quality:
                continue
            for team in teams:
                if team.season != season:
                    continue
                key = (season, team.team_id)
                strengths[key], *_ = team_strength(
                    rosters.get(key, []), quality, minutes,
                    value_model.replacement_pm36,
                )
        if len(strengths) < 60:
            continue
        win_model = fit_win_model(center_by_season(strengths), teams, train)

        quality, minutes = player_quality(value_model, players, later, all_seasons)
        if not quality:
            continue

        for team in teams:
            if team.season != later:
                continue
            before = rosters.get((earlier, team.team_id), [])
            after = rosters.get((later, team.team_id), [])
            previous = by_team.get((earlier, team.team_id))
            if not before or not after or previous is None:
                continue

            # Strength of the old and new rosters, both priced with the SAME
            # quality estimates — the ones available before `later`. That is
            # what isolates the roster change: anything else that differs
            # between the two seasons is held constant by construction.
            old, *_ = team_strength(
                before, quality, minutes, value_model.replacement_pm36
            )
            new, *_ = team_strength(
                after, quality, minutes, value_model.replacement_pm36
            )
            predicted = win_model.slope * (new - old)

            actual = (
                team.wins * 82.0 / max(team.games, 1)
                - previous.wins * 82.0 / max(previous.games, 1)
            )

            def share(roster: list[str]) -> dict[str, float]:
                weights = {
                    pid: minutes.get(pid, ROOKIE_MINUTES_PER_GAME) for pid in roster
                }
                total = sum(weights.values())
                return {p: (w / total if total else 0.0) for p, w in weights.items()}

            share_before, share_after = share(before), share(after)
            turnover = sum(
                v for pid, v in share_after.items() if pid not in set(before)
            )
            out.append(
                Transition(
                    team_id=team.team_id,
                    from_season=earlier,
                    to_season=later,
                    predicted_delta=predicted,
                    actual_delta=actual,
                    turnover=turnover,
                    disruption=_disruption(players, later, team.team_id, rosters),
                )
            )
    return out


@dataclass
class ErrorSummary:
    label: str
    n: int
    mean: float
    sd: float
    mae: float
    p50: float
    p90: float
    correlation: float

    def line(self) -> str:
        return (
            f"  {self.label:<34} n={self.n:>4}  bias {self.mean:+5.2f}  "
            f"sd {self.sd:5.2f}  MAE {self.mae:5.2f}  "
            f"p50 {self.p50:5.2f}  p90 {self.p90:5.2f}  r={self.correlation:+.2f}"
        )


def summarise(label: str, rows: list[Transition]) -> ErrorSummary:
    errors = np.array([t.error for t in rows])
    predicted = np.array([t.predicted_delta for t in rows])
    actual = np.array([t.actual_delta for t in rows])
    correlation = (
        float(np.corrcoef(predicted, actual)[0, 1]) if len(rows) > 2 else float("nan")
    )
    return ErrorSummary(
        label=label,
        n=len(rows),
        mean=float(errors.mean()),
        sd=float(errors.std(ddof=1)) if len(rows) > 1 else 0.0,
        mae=float(np.abs(errors).mean()),
        p50=float(np.percentile(np.abs(errors), 50)),
        p90=float(np.percentile(np.abs(errors), 90)),
        correlation=correlation,
    )


def separation_threshold(sd: float, z: float = 1.0) -> float:
    """Win gap needed to call two options apart, from a measured error sd.

    Two options compared, each carrying this error independently, so the
    difference carries sqrt(2) times it. Same arithmetic as the theoretical
    version — what changes is that the input is now a measurement.
    """
    return z * sd * float(np.sqrt(2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure win-delta error.")
    parser.add_argument("--json", type=str, default=None)
    args = parser.parse_args(argv)

    rows = measure()
    if not rows:
        print("no transitions measured")
        return 1

    moved = [t for t in rows if t.turnover >= MIN_TURNOVER]
    quiet = [t for t in moved if t.disruption < INJURY_GAMES_LOST]
    disrupted = [t for t in moved if t.disruption >= INJURY_GAMES_LOST]

    print("=" * 78)
    print("  win-delta error, measured against actual season-to-season changes")
    print("=" * 78)
    print(summarise("all transitions", rows).line())
    print(summarise(f"turnover >= {MIN_TURNOVER:.0%}", moved).line())
    print(summarise(f"  of which low disruption", quiet).line())
    print(summarise(f"  of which high disruption", disrupted).line())

    print("\n  by turnover band:")
    for low, high in ((0.05, 0.15), (0.15, 0.30), (0.30, 1.01)):
        band = [t for t in moved if low <= t.turnover < high]
        if band:
            print(summarise(f"    turnover {low:.0%}-{high:.0%}", band).line())

    baseline = summarise("baseline: always predict 0", rows)
    zeros = np.array([t.actual_delta for t in rows])
    print(f"\n  a model that always predicted 0 would have "
          f"MAE {np.abs(zeros).mean():.2f}, sd {zeros.std(ddof=1):.2f}")

    best = summarise("x", quiet or moved)
    print("\n" + "=" * 78)
    print(f"  measured error sd: {best.sd:.2f} wins "
          f"(low-disruption subset, n={best.n})")
    print(f"  separation threshold at z=1: {separation_threshold(best.sd):.1f} wins")
    print("=" * 78)

    if args.json:
        import json
        from pathlib import Path

        Path(args.json).write_text(json.dumps({
            "n": len(rows),
            "all": summarise("all", rows).__dict__,
            "moved": summarise("moved", moved).__dict__,
            "low_disruption": summarise("quiet", quiet).__dict__ if quiet else None,
            "high_disruption": summarise("disrupted", disrupted).__dict__ if disrupted else None,
            "threshold_z1": separation_threshold(best.sd),
        }, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
