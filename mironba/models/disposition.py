"""Buyer or seller: the central midseason decision, derived rather than guessed.

At a deadline, the first thing a front office settles is which side of the
market it is on. Nothing in this codebase modelled it, and the tempting fix is
to ask an LLM — which would produce a confident answer with no evidence behind
it and no way to check it.

So it comes from the standings, computed from dated game results. A team's
record on a given date is a filter over ``game_logs.csv``, not an inference.

**The value model is deliberately not consulted.** Disposition depends on
record and games back on the freeze date, which are completed facts. The
earlier version applied the value model's 10.5-win threshold here, and that was
the wrong error bar: 10.5 is uncertainty on a counterfactual roster delta, not
on an observed standing. It sent 23 of 30 teams to AMBIGUOUS and, because only
buyers and sellers acted, made the simulation miss every deal between middling
teams. ``test_disposition_never_consults_the_value_model`` keeps it out.

The bands are measured instead — see ``SELLER_GAMES_BACK``. Teams still land in
AMBIGUOUS when the direction is genuinely open, but ambiguous is not a synonym
for inactive: those teams consolidate, move expiring salary and take flyers.
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

#: What an ambiguous team does. It is NOT "nothing": a team that cannot tell
#: whether it is buying still consolidates, moves expiring salary, and takes
#: flyers on players others have given up on. Standing pat was an artifact of
#: the old gate, not a behaviour anyone chose.
AMBIGUOUS_ACTS = True

#: Games out of a playoff place beyond which a team is a clear seller.
#:
#: **Measured from observed standings, not from the value model.** The earlier
#: version used the 10.5-win separation threshold, and that was the wrong error
#: bar entirely: 10.5 is uncertainty on a *counterfactual roster delta*, while
#: disposition depends on record and games back on the freeze date, which are
#: completed facts with no projection in them. Applying projection uncertainty
#: to an observed quantity sent 23 of 30 teams to AMBIGUOUS and, since only
#: buyers and sellers acted, made the simulation miss every deal between
#: middling teams.
#:
#: Measured over 90 team-seasons across the 2023, 2024 and 2025 deadlines —
#: games back at the deadline against whether the team finished top ten in its
#: conference:
#:
#:     8+ games clear    20 teams   100% made it
#:     4-8 clear         17 teams   100%
#:     0-4 clear         20 teams    85%
#:     0-3 back          15 teams    40%
#:     3-6 back           4 teams      0%
#:     6+ back           14 teams      0%
#:
#: The edges are clean: nobody 4+ games clear missed, nobody 3+ back made it.
#: Those are the bands, and they are much tighter than 10 because an observed
#: standing is a far better-determined quantity than a projected win delta.
SELLER_GAMES_BACK = 3.0
BUYER_GAMES_AHEAD = 4.0

#: Where the empirical bands came from, so the numbers are not mistaken for
#: taste. Recomputable with ``python -m mironba.models.disposition --calibrate``.
BAND_PROVENANCE = (
    "measured, 90 team-seasons over three deadlines; 37/37 of teams 4+ games "
    "clear made the top ten and 18/18 of teams 3+ back did not"
)

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
                "inside the band where historically 40-85% of teams made the "
                "playoffs, so the direction is genuinely open"
            )
        out[team] = Disposition(team, side, back, standing, reason)
    return out


def summarise(dispositions: dict[str, Disposition]) -> str:
    counts: dict[str, int] = {}
    for value in dispositions.values():
        counts[value.side] = counts.get(value.side, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
