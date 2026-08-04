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
    "2025-26",
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


GAME_FIELDS = (
    "TEAM_ID", "TEAM_ABBREVIATION", "GAME_ID", "GAME_DATE", "MATCHUP", "WL",
    "PTS", "PLUS_MINUS",
)


def fetch_game_log(season: str, *, timeout: int = 120) -> list[dict]:
    """Every regular-season game, dated, one row per team.

    This is what makes an in-season scenario possible at all: with it, a team's
    record on any given date is a filter rather than a guess. Without it the
    only available standings are end-of-season, which is the answer the
    simulation is supposed to be predicting.
    """
    from nba_api.stats.endpoints import leaguegamelog

    _throttle()
    try:
        frame = leaguegamelog.LeagueGameLog(
            season=season, season_type_all_star="Regular Season", timeout=timeout
        ).get_data_frames()[0]
    except Exception as exc:  # noqa: BLE001
        raise StatsFetchError(f"{season} games: {type(exc).__name__}: {exc}") from exc
    if len(frame) < 1000:
        raise StatsFetchError(f"{season}: only {len(frame)} game rows")
    return frame[list(GAME_FIELDS)].to_dict("records")


PLAYER_GAME_FIELDS = ("PLAYER_ID", "PLAYER_NAME", "TEAM_ABBREVIATION",
                      "GAME_ID", "GAME_DATE", "MIN")


def fetch_player_game_log(season: str, *, timeout: int = 120) -> list[dict]:
    """Every regular-season player appearance, dated. Same source, new table.

    This is what as-of availability derives from: a player with zero
    appearances in his team's last N games before a date was unavailable,
    whatever the reason. The team log cannot say that - it has no player
    column - and no new *source* is involved: it is the same stats endpoint
    the team log already uses, asked for player rows.
    """
    from nba_api.stats.endpoints import leaguegamelog

    _throttle()
    try:
        frame = leaguegamelog.LeagueGameLog(
            season=season, season_type_all_star="Regular Season",
            player_or_team_abbreviation="P", timeout=timeout,
        ).get_data_frames()[0]
    except Exception as exc:  # noqa: BLE001
        raise StatsFetchError(f"{season} player games: {type(exc).__name__}: {exc}") from exc
    if len(frame) < 10000:
        raise StatsFetchError(f"{season}: only {len(frame)} player-game rows")
    return frame[list(PLAYER_GAME_FIELDS)].to_dict("records")


def write_player_game_logs(logs: dict, root: Path = SNAPSHOT_ROOT) -> Path:
    """Write player game logs, **merging** with seasons already on disk."""
    directory = root / "nba-stats"
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / "player_game_logs.csv"
    merged: dict[str, list[dict]] = {}
    if existing.is_file():
        with existing.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                merged.setdefault(row["season"], []).append(row)
    merged.update(logs)

    with existing.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("season", *PLAYER_GAME_FIELDS))
        for season, rows in sorted(merged.items()):
            for row in rows:
                writer.writerow((season, *(row.get(f) for f in PLAYER_GAME_FIELDS)))
    return directory


#: Writers whose input is season-partitioned. These MUST merge with what is
#: already stored: opening "w" with only the current pull destroys every other
#: season, and does it silently - the file still parses and the loader still
#: loads. Three writers here had that defect, found one at a time.
PARTITIONED = frozenset({"write_game_logs", "write_player_game_logs", "write_snapshot"})

#: The absent-writer check (entry #62): every function that acquires data at
#: cost declares how that data reaches disk BEFORE the next fallible
#: operation. Enforced by enumeration in tests/test_writers_merge.py.
ACQUIRERS = {
    "fetch_season": ("persists-per-unit",
                     "main() writes each SeasonPull via write_snapshot the "
                     "moment it returns"),
    "fetch_game_log": ("persists-per-unit",
                       "main() writes each season via write_game_logs inside "
                       "the loop"),
    "fetch_player_game_log": ("persists-per-unit",
                              "callers write via write_player_game_logs per "
                              "season (merging writer)"),
    "fetch_careers": ("persists-per-unit",
                      "single fetch, written immediately by main()"),
}

#: Writers that legitimately replace the whole table, because their input is
#: the whole table. Declared so a new writer cannot be ambiguous about which
#: kind it is; tests/test_writers_merge.py fails on anything undeclared.
WHOLE_TABLE = frozenset({"write_careers"})


def write_game_logs(logs: dict, root: Path = SNAPSHOT_ROOT) -> Path:
    """Write game logs, **merging** with seasons already on disk.

    This used to open the file "w" with only the seasons just fetched, so
    ``--games --seasons 2025-26`` silently destroyed the other three. That is a
    bad failure: the file still parses, the sim still runs, and standings for
    every earlier season quietly become empty. Seasons in ``logs`` replace
    their stored version; seasons absent from it are carried through untouched.
    """
    directory = root / "nba-stats"
    directory.mkdir(parents=True, exist_ok=True)
    existing = directory / "game_logs.csv"
    merged: dict[str, list[dict]] = {}
    if existing.is_file():
        with existing.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                merged.setdefault(row["season"], []).append(row)
    merged.update(logs)
    logs = merged

    with (directory / "game_logs.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("season", *GAME_FIELDS))
        for season, rows in sorted(logs.items()):
            for row in rows:
                writer.writerow((season, *(row.get(f) for f in GAME_FIELDS)))
    return directory


CAREER_FIELDS = ("PERSON_ID", "DISPLAY_FIRST_LAST", "FROM_YEAR", "TO_YEAR")


def fetch_careers(season: str = "2024-25", *, timeout: int = 120) -> list[dict]:
    """First and last season for every player in league history.

    Service years are what the minimum-salary scale keys on, and nothing in the
    Basketball-Reference ingest carries them. Without this the validator falls
    back to the zero-experience minimum for everyone, which refuses the
    minimum-salary exception to players who qualify for it and rejects real
    trades. One call returns every player who has ever appeared.
    """
    from nba_api.stats.endpoints import commonallplayers

    _throttle()
    try:
        frame = commonallplayers.CommonAllPlayers(
            is_only_current_season=0, season=season, timeout=timeout
        ).get_data_frames()[0]
    except Exception as exc:  # noqa: BLE001
        raise StatsFetchError(f"careers: {type(exc).__name__}: {exc}") from exc
    if len(frame) < 4000:
        raise StatsFetchError(f"careers: only {len(frame)} players")
    return frame[list(CAREER_FIELDS)].to_dict("records")


def write_careers(rows: list[dict], root: Path = SNAPSHOT_ROOT) -> Path:
    directory = root / "nba-stats"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "careers.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CAREER_FIELDS)
        for row in rows:
            writer.writerow([row.get(f) for f in CAREER_FIELDS])
    return directory


def write_snapshot(pulls: list[SeasonPull], root: Path = SNAPSHOT_ROOT) -> Path:
    directory = root / "nba-stats"
    directory.mkdir(parents=True, exist_ok=True)

    def dump(name: str, fields: tuple[str, ...], rows: list[tuple[str, dict]]) -> None:
        """Write, **merging** with seasons already on disk.

        This opened "w" with only the seasons just pulled, so
        ``--seasons 2012-13 2013-14`` replaced an eleven-season table with a
        two-season one. Nothing raised: the file still parsed, the value model
        still loaded, and every season silently returned zero valued players.
        The same defect was fixed in write_game_logs and not looked for here.

        Seasons in this pull replace their stored version; seasons absent from
        it are carried through untouched.
        """
        target = directory / name
        stored: dict[str, list[dict]] = {}
        if target.is_file():
            with target.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    stored.setdefault(row["season"], []).append(row)
        for season in {s for s, _ in rows}:
            stored.pop(season, None)
        merged = [(season, row) for season, rs in stored.items() for row in rs]
        merged += rows
        merged.sort(key=lambda pair: pair[0])
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("season", *fields))
            for season, row in merged:
                writer.writerow((season, *(row.get(f) for f in fields)))

    dump("player_seasons.csv", PLAYER_FIELDS,
         [(p.season, r) for p in pulls for r in p.players])
    dump("team_seasons.csv", TEAM_FIELDS,
         [(p.season, r) for p in pulls for r in p.teams])

    # Merged for the same reason as the data tables, and it is worse here: this
    # is the provenance record. Losing it does not lose numbers, it loses the
    # answer to "where did this season come from", which is the one thing this
    # project refuses to leave unanswered. It was silently down to two seasons
    # of provenance for thirteen seasons of data.
    sources_path = directory / "sources.csv"
    kept: list[list[str]] = []
    pulled = {p.season for p in pulls}
    if sources_path.is_file():
        with sources_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            kept = [row for row in reader if len(row) > 1 and row[1] not in pulled]

    with sources_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["table", "scope", "source_url", "retrieved_date"])
        for row in kept:
            writer.writerow(row)
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
    parser.add_argument("--games", action="store_true",
                        help="fetch dated game logs instead of season totals")
    parser.add_argument("--careers", action="store_true",
                        help="fetch first/last season per player (service years)")
    args = parser.parse_args(argv)

    if args.careers:
        rows = fetch_careers()
        print(f"  {len(rows)} players")
        print(f"OK  careers -> {write_careers(rows)}")
        return 0

    if args.games:
        written, failures = 0, []
        for season in args.seasons:
            try:
                rows = fetch_game_log(season)
            except StatsFetchError as exc:
                failures.append(str(exc))
                print(f"  ! {exc}", flush=True)
                continue
            dates = [r["GAME_DATE"] for r in rows]
            # Persist THIS season before the next fetch can fail the run:
            # cost-acquired data never waits in memory across a fallible
            # operation (entry #62 - the 991 discarded GDELT articles).
            write_game_logs({season: rows}, root=SNAPSHOT_ROOT)
            written += 1
            print(f"  {season}: {len(rows)} team-games, "
                  f"{min(dates)} -> {max(dates)} -> written", flush=True)
        if not written:
            return 1
        print(f"\nOK  {written} season(s) -> {SNAPSHOT_ROOT / 'nba-stats'}")
        return 0 if not failures else 1

    written = 0
    failures = []
    directory = None
    for season in args.seasons:
        try:
            pull = fetch_season(season)
        except StatsFetchError as exc:
            failures.append(str(exc))
            print(f"  ! {exc}", flush=True)
            continue
        # Persist immediately: write_snapshot merges per season, so a later
        # crash costs nothing already fetched (entry #62).
        directory = write_snapshot([pull], root=SNAPSHOT_ROOT)
        written += 1
        print(f"  {season}: {len(pull.players)} players, {len(pull.teams)} "
              "teams -> written", flush=True)

    if failures or not written:
        print(f"\nSKIPPED/partial: {len(failures)} season(s) failed, "
              f"{written} written")
        return 1

    print(f"\nOK  {written} seasons -> {directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
