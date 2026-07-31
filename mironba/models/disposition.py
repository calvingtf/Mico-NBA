"""Buyer or seller: the central midseason decision, derived rather than guessed.

At a deadline, the first thing a front office settles is which side of the
market it is on. Nothing in this codebase modelled it, and the tempting fix is
to ask an LLM — which would produce a confident answer with no evidence behind
it and no way to check it.

So it comes from the standings, computed from dated game results. A team's
record on a given date is a filter over ``game_logs.csv``, not an inference.

**The 10.5-win threshold applies here too.** A team twelve games out of a
playoff place is distinguishable from a contender; two teams a game apart are
not. Disposition is therefore a *band*, not a ranking, and the band boundaries
are set from the measured delta error rather than chosen. Teams that fall
between bands come back ``AMBIGUOUS`` — which is a real answer, and one a
deadline simulation should act on by doing less rather than by guessing.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "data" / "snapshots" / "nba-stats"

BUYER = "buyer"
SELLER = "seller"
AMBIGUOUS = "ambiguous"

#: Games out of a playoff place beyond which a team is a clear seller.
#:
#: Derived, not chosen. ``models/delta_error.py`` measured the win-delta error
#: at 7.4 and the separation threshold at 10.5 wins over a full season. A
#: deadline sits at roughly 75% of the schedule, so the remaining quarter can
#: move a team by about a quarter of that — call it 2.6 games — and a gap has
#: to exceed the full-season threshold to be a distinction the model supports.
#: 10.5 games back is used directly, rounded to 10.
SELLER_GAMES_BACK = 10.0

#: Inside a playoff place by this much and a team is a clear buyer. Same
#: reasoning, same number, other direction.
BUYER_GAMES_AHEAD = 10.0

#: Playoff places per conference, including the play-in.
PLAYOFF_PLACES = 10


@dataclass(frozen=True, slots=True)
class Standing:
    team: str
    wins: int
    losses: int
    games_played: int

    @property
    def win_pct(self) -> float:
        return self.wins / self.games_played if self.games_played else 0.0

    @property
    def pace(self) -> float:
        """Projected 82-game wins at the current rate. Not a forecast."""
        return self.win_pct * 82.0


@dataclass(frozen=True, slots=True)
class Disposition:
    team: str
    side: str
    games_back: float
    standing: Standing
    reason: str

    def line(self) -> str:
        return (
            f"  {self.team:5} {self.standing.wins:>2}-{self.standing.losses:<2} "
            f"({self.standing.pace:4.1f} pace)  {self.side:<9} {self.reason}"
        )


def standings_on(
    season: str, when: date, root: Path = SNAPSHOT
) -> dict[str, Standing]:
    """Every team's record as of the end of ``when``.

    A filter over dated game rows. This is the whole reason the game log was
    ingested: without it the only available record is end-of-season, which is
    part of what a deadline scenario is supposed to predict.
    """
    path = root / "game_logs.csv"
    if not path.is_file():
        return {}
    wins: dict[str, int] = {}
    losses: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["season"] != season:
                continue
            played = date.fromisoformat(row["GAME_DATE"])
            if played > when:
                continue
            team = row["TEAM_ABBREVIATION"]
            if row["WL"] == "W":
                wins[team] = wins.get(team, 0) + 1
            else:
                losses[team] = losses.get(team, 0) + 1
    teams = set(wins) | set(losses)
    return {
        team: Standing(team, wins.get(team, 0), losses.get(team, 0),
                       wins.get(team, 0) + losses.get(team, 0))
        for team in sorted(teams)
    }


#: Conference membership, needed because playoff position is per conference.
#: From the teams table in every snapshot; hardcoded here to avoid a database
#: round trip inside a hot loop, and asserted against the snapshot in tests.
EAST = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DET", "IND",
    "MIA", "MIL", "NYK", "ORL", "PHI", "TOR", "WAS",
}


def games_back_of_playoffs(
    standings: dict[str, Standing], team: str
) -> float:
    """Games behind the last playoff place in this team's conference.

    Negative means inside it — the margin over the first team outside. Games
    back is the standard ``((leader_w - w) + (l - leader_l)) / 2``.
    """
    conference = EAST if team in EAST else set(standings) - EAST
    ranked = sorted(
        (standings[t] for t in standings if t in conference),
        key=lambda s: (-s.win_pct, -s.wins),
    )
    if len(ranked) < PLAYOFF_PLACES + 1:
        return 0.0
    cutoff = ranked[PLAYOFF_PLACES - 1]
    first_out = ranked[PLAYOFF_PLACES]
    mine = standings[team]
    inside = any(s.team == team for s in ranked[:PLAYOFF_PLACES])
    # Measured against the first team OUTSIDE if we are in, and against the
    # last team IN if we are out. Either way the standard formula already
    # carries the sign: a team ahead of its reference gets a negative figure.
    # Negating it again for inside teams put Oklahoma City at 40-9 fifteen
    # games "out of a playoff place" and labelled the best team in the league
    # a seller.
    reference = first_out if inside else cutoff
    return ((reference.wins - mine.wins) + (mine.losses - reference.losses)) / 2.0


def disposition(
    season: str, when: date, root: Path = SNAPSHOT
) -> dict[str, Disposition]:
    """Buyer, seller, or ambiguous, for every team, on a given date."""
    standings = standings_on(season, when, root)
    out: dict[str, Disposition] = {}
    for team, standing in standings.items():
        back = games_back_of_playoffs(standings, team)
        if back >= SELLER_GAMES_BACK:
            side, reason = SELLER, f"{back:.1f} games out of a playoff place"
        elif back <= -BUYER_GAMES_AHEAD:
            side, reason = BUYER, f"{-back:.1f} games clear of the cut"
        else:
            side = AMBIGUOUS
            reason = (
                f"{abs(back):.1f} games {'out' if back > 0 else 'clear'} — "
                f"inside the {SELLER_GAMES_BACK:.0f}-game band the value model "
                "cannot resolve"
            )
        out[team] = Disposition(team, side, back, standing, reason)
    return out


def summarise(dispositions: dict[str, Disposition]) -> str:
    counts: dict[str, int] = {}
    for value in dispositions.values():
        counts[value.side] = counts.get(value.side, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
