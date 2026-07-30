"""Build real snapshots from Basketball-Reference.

Run: ``python -m mironba.data.ingest.build --seasons 2023-24 2024-25 2025-26``

Refuses to write a partial season. If any of the 30 team pages or the
transaction log for a season cannot be retrieved or parsed, that season is
skipped entirely and reported. A snapshot missing eight teams would still load
and would still produce apron tiers — wrong ones — so the failure has to be
loud at ingest time rather than silent at query time.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mironba.data.ingest import bbref
from mironba.data.ingest.cache import FetchError, Fetched, fetch

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = REPO_ROOT / "data_cache"
SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "snapshots"

DEFAULT_SEASONS = ("2023-24", "2024-25", "2025-26")

TEAM_META = {
    "ATL": ("Hawks", "Atlanta", "East", "Southeast"),
    "BOS": ("Celtics", "Boston", "East", "Atlantic"),
    "BKN": ("Nets", "Brooklyn", "East", "Atlantic"),
    "CHA": ("Hornets", "Charlotte", "East", "Southeast"),
    "CHI": ("Bulls", "Chicago", "East", "Central"),
    "CLE": ("Cavaliers", "Cleveland", "East", "Central"),
    "DAL": ("Mavericks", "Dallas", "West", "Southwest"),
    "DEN": ("Nuggets", "Denver", "West", "Northwest"),
    "DET": ("Pistons", "Detroit", "East", "Central"),
    "GSW": ("Warriors", "Golden State", "West", "Pacific"),
    "HOU": ("Rockets", "Houston", "West", "Southwest"),
    "IND": ("Pacers", "Indiana", "East", "Central"),
    "LAC": ("Clippers", "Los Angeles", "West", "Pacific"),
    "LAL": ("Lakers", "Los Angeles", "West", "Pacific"),
    "MEM": ("Grizzlies", "Memphis", "West", "Southwest"),
    "MIA": ("Heat", "Miami", "East", "Southeast"),
    "MIL": ("Bucks", "Milwaukee", "East", "Central"),
    "MIN": ("Timberwolves", "Minnesota", "West", "Northwest"),
    "NOP": ("Pelicans", "New Orleans", "West", "Southwest"),
    "NYK": ("Knicks", "New York", "East", "Atlantic"),
    "OKC": ("Thunder", "Oklahoma City", "West", "Northwest"),
    "ORL": ("Magic", "Orlando", "East", "Southeast"),
    "PHI": ("76ers", "Philadelphia", "East", "Atlantic"),
    "PHX": ("Suns", "Phoenix", "West", "Pacific"),
    "POR": ("Trail Blazers", "Portland", "West", "Northwest"),
    "SAC": ("Kings", "Sacramento", "West", "Pacific"),
    "SAS": ("Spurs", "San Antonio", "West", "Southwest"),
    "TOR": ("Raptors", "Toronto", "East", "Atlantic"),
    "UTA": ("Jazz", "Utah", "West", "Northwest"),
    "WAS": ("Wizards", "Washington", "East", "Southeast"),
}


@dataclass
class SeasonResult:
    season: str
    ok: bool
    teams_ingested: int = 0
    players: int = 0
    transactions: int = 0
    trades: int = 0
    failures: list[str] = None

    def __post_init__(self) -> None:
        if self.failures is None:
            self.failures = []


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def ingest_season(season: str, *, force: bool = False) -> SeasonResult:
    result = SeasonResult(season=season, ok=False)
    salaries: list[bbref.SalaryRow] = []
    sources: list[list[str]] = []

    for code in bbref.BBREF_CODES:
        url = bbref.team_season_url(code, season)
        try:
            page: Fetched = fetch(url, CACHE_DIR, force=force)
        except FetchError as exc:
            result.failures.append(str(exc))
            continue

        rows = bbref.parse_team_salaries(page.text, code, season)
        if not rows:
            result.failures.append(f"{url} -> no salary table found")
            continue
        salaries.extend(rows)
        sources.append(["salaries", bbref.TEAM_CODE[code], url, page.retrieved_date])

    transactions: list[bbref.Transaction] = []
    tx_url = bbref.transactions_url(season)
    try:
        tx_page = fetch(tx_url, CACHE_DIR, force=force)
        transactions = bbref.parse_transactions(tx_page.text, season)
        if not transactions:
            result.failures.append(f"{tx_url} -> no transactions parsed")
        else:
            sources.append(["transactions", "", tx_url, tx_page.retrieved_date])
    except FetchError as exc:
        result.failures.append(str(exc))

    teams_seen = {r.team_id for r in salaries}
    result.teams_ingested = len(teams_seen)
    result.players = len({r.player_id for r in salaries})
    result.transactions = len(transactions)
    result.trades = sum(1 for t in transactions if t.is_trade)

    if len(teams_seen) < 30 or not transactions:
        result.failures.append(
            f"incomplete: {len(teams_seen)}/30 teams, {len(transactions)} transactions"
        )
        return result

    _write_snapshot(season, salaries, transactions, sources)
    result.ok = True
    return result


def _write_snapshot(
    season: str,
    salaries: list[bbref.SalaryRow],
    transactions: list[bbref.Transaction],
    sources: list[list[str]],
) -> Path:
    directory = SNAPSHOT_ROOT / f"bbref-{season}"
    directory.mkdir(parents=True, exist_ok=True)

    _write_csv(
        directory / "teams.csv",
        ["team_id", "name", "city", "conference", "division"],
        [[code, *TEAM_META[code]] for code in sorted(TEAM_META)],
    )

    # A player can appear on two teams in one season after a trade. Keep every
    # row for the contracts table, but emit one players row per player.
    names: dict[str, str] = {}
    for row in salaries:
        names.setdefault(row.player_id, row.name)
    _write_csv(
        directory / "players.csv",
        ["player_id", "name", "position", "birth_date"],
        [[pid, name, "", ""] for pid, name in sorted(names.items())],
    )

    # Deduplicate to the highest salary per player-season. Basketball-Reference
    # lists a traded player under both teams; the contracts table is keyed on
    # (player, season), so one row has to win.
    best: dict[str, bbref.SalaryRow] = {}
    for row in salaries:
        current = best.get(row.player_id)
        if current is None or row.salary > current.salary:
            best[row.player_id] = row

    _write_csv(
        directory / "contracts.csv",
        [
            "player_id", "team_id", "season", "salary", "guaranteed",
            "contract_type", "signed_on", "acquired_via_trade_on",
            "trade_restricted_until", "no_trade_clause", "outgoing_match_value",
            "re_sign_status", "previous_salary",
        ],
        [
            [r.player_id, r.team_id, r.season, r.salary, r.salary,
             "standard", "", "", "", 0, "", "unknown", ""]
            for r in sorted(best.values(), key=lambda r: (r.team_id, -r.salary))
        ],
    )

    _write_csv(
        directory / "transactions.csv",
        ["date", "season", "is_trade", "team_ids", "player_ids", "text", "marked_text"],
        [
            [t.date.isoformat(), t.season, int(t.is_trade),
             "|".join(t.team_ids), "|".join(t.player_ids), t.text, t.marked_text]
            for t in transactions
        ],
    )

    _write_csv(
        directory / "sources.csv",
        ["table", "scope", "source_url", "retrieved_date"],
        sources,
    )

    retrieved = {row[3] for row in sources}
    (directory / "snapshot.yaml").write_text(
        f"""snapshot_id: bbref-{season}
as_of_date: "{max(retrieved)}"
season: "{season}"
source: basketball-reference.com
notes: >
  Ingested by mironba.data.ingest.build. Per-player salaries come from each
  team's season page (the 'salaries2' table); transactions from the season
  transaction log. Per-table source URLs and retrieval dates are in
  sources.csv.

  Known limits, which matter for how this data may be used:

  re_sign_status is 'unknown' for every player — Basketball-Reference does not
  publish whether a contract was a re-signing using Bird rights. The validator
  therefore returns UNDETERMINED for base-year compensation on any trade built
  from this snapshot. That is correct, not a defect: the information genuinely
  is not here, and it must be supplied by hand before a fixture derived from
  this data can be marked verified.

  Salaries are season cap hits, not cap hits as of a particular date, and a
  player traded mid-season is listed by both teams. The contracts table keeps
  the higher figure per player-season. Summed team payroll is therefore an
  approximation of apron salary and excludes dead money and cap holds — see
  team_salary_estimate() in mironba/data/candidates.py.
""",
        encoding="utf-8",
    )
    return directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest real NBA data.")
    parser.add_argument("--seasons", nargs="+", default=list(DEFAULT_SEASONS))
    parser.add_argument("--force", action="store_true", help="bypass the disk cache")
    args = parser.parse_args(argv)

    results = [ingest_season(s, force=args.force) for s in args.seasons]

    print("\n=== ingest summary ===")
    for r in results:
        status = "OK     " if r.ok else "SKIPPED"
        print(
            f"{status} {r.season}: {r.teams_ingested}/30 teams, {r.players} players, "
            f"{r.transactions} transactions ({r.trades} trade entries)"
        )
        for failure in r.failures[:6]:
            print(f"         ! {failure}")
        if len(r.failures) > 6:
            print(f"         ! ... and {len(r.failures) - 6} more")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
