"""Player and team box-score seasons, from the NBA's own stats endpoints.

Basketball-Reference supplies the money; this supplies the basketball. The
split is not arbitrary — ``stats.nba.com`` is the league's own API and carries
no redistribution problem of the kind that keeps every scraped salary table out
of this repo, so these tables *are* committed. That is what makes the value
model reproducible from a clone.

    python -m mironba.data.ingest.nba_stats --seasons 2014-15 ... 2024-25

Two endpoints, one call each per season:

  LeagueDashPlayerStats   per-player regular-season totals, 67 columns
  LeagueDashTeamStats     per-team totals including W/L, which is the target

Provenance is recorded the same way as the Basketball-Reference ingest: a
sources.csv naming the endpoint, the parameters and the retrieval date, and a
snapshot.yaml stating what the tables are and what they are not. The endpoint
is a live query rather than a document, so "source_url" is the endpoint plus
its parameters — enough to reissue exactly the same request.

Rate limiting is deliberate and generous. stats.nba.com throttles aggressively
and returns a hanging connection rather than a clean 429, which is the worst
kind of failure to diagnose. A season is one request; a decade is twenty.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "snapshots"

#: Seasons to ingest by default. Ten complete regular seasons, deliberately
#: skipping 2019-20 and 2020-21 in the *model* rather than here — they are
#: ingested so the exclusion is visible and reversible, not hidden by absence.
DEFAULT_SEASONS = (
    "2014-15", "2015-16", "2016-17", "2017-18", "2018-19",
    "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25",
)

#: stats.nba.com will hang rather than 429 under load. One request per season
#: per endpoint means this costs seconds, not minutes.
MIN_INTERVAL_S = 1.5

PLAYER_FIELDS = (
    "PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION", "AGE",
    "GP", "W", "L", "MIN",
    "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
    "OREB", "DREB", "REB", "AST", "TOV", "STL", "BLK", "BLKA", "PF", "PFD",
    "PTS", "PLUS_MINUS",
)

TEAM_FIELDS = (
    "TEAM_ID", "TEAM_NAME", "GP", "W", "L", "W_PCT", "MIN",
    "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA",
    "OREB", "DREB", "REB", "AST", "TOV", "STL", "BLK", "PF",
    "PTS", "PLUS_MINUS",
)

_last_request = 0.0


class StatsFetchError(RuntimeError):
    """A season could not be retrieved. Never replaced with a partial table."""


def _throttle() -> None:
    global _last_request
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()


@dataclass
class SeasonPull:
    season: str
    players: list[dict]
    teams: list[dict]
    retrieved_at: str

    @property
    def retrieved_date(self) -> str:
        return self.retrieved_at[:10]


def fetch_season(season: str, *, timeout: int = 120) -> SeasonPull:
    """One season of player and team totals.

    Raises rather than returning a short table. A season missing teams would
    still fit a regression and would still produce a MAE — a wrong one.
    """
    from nba_api.stats.endpoints import (
        leaguedashplayerstats,
        leaguedashteamstats,
    )

    _throttle()
    try:
        players = leaguedashplayerstats.LeagueDashPlayerStats(
            season=season, season_type_all_star="Regular Season", timeout=timeout
        ).get_data_frames()[0]
    except Exception as exc:  # noqa: BLE001 - surface the cause verbatim
        raise StatsFetchError(f"{season} players: {type(exc).__name__}: {exc}") from exc

    _throttle()
    try:
        teams = leaguedashteamstats.LeagueDashTeamStats(
            season=season, season_type_all_star="Regular Season", timeout=timeout
        ).get_data_frames()[0]
    except Exception as exc:  # noqa: BLE001
        raise StatsFetchError(f"{season} teams: {type(exc).__name__}: {exc}") from exc

    if len(teams) != 30:
        raise StatsFetchError(f"{season}: {len(teams)} teams, expected 30")
    if len(players) < 300:
        raise StatsFetchError(f"{season}: only {len(players)} players")

    return SeasonPull(
        season=season,
        players=players[list(PLAYER_FIELDS)].to_dict("records"),
        teams=teams[list(TEAM_FIELDS)].to_dict("records"),
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def write_snapshot(pulls: list[SeasonPull], root: Path = SNAPSHOT_ROOT) -> Path:
    directory = root / "nba-stats"
    directory.mkdir(parents=True, exist_ok=True)

    def dump(name: str, fields: tuple[str, ...], rows: list[tuple[str, dict]]) -> None:
        with (directory / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("season", *fields))
            for season, row in rows:
                writer.writerow((season, *(row.get(f) for f in fields)))

    dump("player_seasons.csv", PLAYER_FIELDS,
         [(p.season, r) for p in pulls for r in p.players])
    dump("team_seasons.csv", TEAM_FIELDS,
         [(p.season, r) for p in pulls for r in p.teams])

    with (directory / "sources.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["table", "scope", "source_url", "retrieved_date"])
        for pull in pulls:
            for table, endpoint in (
                ("player_seasons", "leaguedashplayerstats"),
                ("team_seasons", "leaguedashteamstats"),
            ):
                writer.writerow([
                    table, pull.season,
                    f"https://stats.nba.com/stats/{endpoint}"
                    f"?Season={pull.season}&SeasonType=Regular+Season",
                    pull.retrieved_date,
                ])

    seasons = [p.season for p in pulls]
    retrieved = max(p.retrieved_date for p in pulls)
    (directory / "snapshot.yaml").write_text(
        f"""snapshot_id: nba-stats
as_of_date: "{retrieved}"
seasons: {seasons}
source: stats.nba.com (NBA official)
notes: >
  Per-player and per-team regular-season totals. Unlike the Basketball-
  Reference tables, these ARE committed: stats.nba.com is the league's own
  API and carries no redistribution restriction of the kind that keeps
  scraped salary data out of this repo. Committing them is what lets the
  value model be refit from a clone.

  Regular season only. Playoff performance is excluded deliberately — the
  target is regular-season wins, and mixing the two would train on games
  that are not being predicted.

  MIN is total minutes and is a float: the NBA's own totals carry fractional
  minutes. GP is games played, not games available; a player who was injured
  simply has fewer of both, and nothing here distinguishes "rested" from
  "hurt" from "benched". That is the availability limitation recorded in the
  README, and it is a property of the source, not of the ingest.

  PLUS_MINUS is raw on-court point differential, not an adjusted metric. It
  is heavily team-dependent: a fifth option on a good team outscores a first
  option on a bad one. models/value.py uses it as a regression target rather
  than as a value metric for exactly that reason.

  2019-20 and 2020-21 are ingested but excluded by the value model: both were
  shortened and one was played in a bubble. The exclusion lives in the model
  so it is visible and reversible, rather than being hidden by absence here.
""",
        encoding="utf-8",
    )
    return directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest NBA box-score seasons.")
    parser.add_argument("--seasons", nargs="+", default=list(DEFAULT_SEASONS))
    args = parser.parse_args(argv)

    pulls: list[SeasonPull] = []
    failures: list[str] = []
    for season in args.seasons:
        try:
            pull = fetch_season(season)
        except StatsFetchError as exc:
            failures.append(str(exc))
            print(f"  ! {exc}", flush=True)
            continue
        pulls.append(pull)
        print(f"  {season}: {len(pull.players)} players, {len(pull.teams)} teams",
              flush=True)

    if failures or not pulls:
        print(f"\nSKIPPED: {len(failures)} season(s) failed")
        return 1

    directory = write_snapshot(pulls)
    print(f"\nOK  {len(pulls)} seasons -> {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
