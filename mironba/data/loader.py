"""CSV snapshot -> SQLite, and SQLite -> rules-engine inputs.

A snapshot directory is a set of CSVs plus a ``snapshot.yaml`` describing where
the data came from and what date it reflects. The loader is strict: a missing
required column or an unparseable dollar figure raises rather than defaulting,
because a silently-zeroed salary produces a trade verdict that is confidently
wrong, which is worse than a crash.
"""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from mironba.rules.cap import TeamSalaryState, TradeException
from mironba.rules.trade_validator import PlayerAsset, ReSignStatus, TeamTradeState


class SnapshotError(ValueError):
    """Raised when snapshot data is missing, malformed, or inconsistent."""


@dataclass(frozen=True, slots=True)
class SnapshotMeta:
    snapshot_id: str
    as_of_date: str
    season: str
    source: str
    notes: str = ""


def parse_money(raw: str, *, field: str, row: int) -> int:
    """Parse a dollar figure to integer dollars.

    Accepts ``"12,345,678"``, ``"$12345678"``, ``"12345678.00"``. Rejects
    anything else loudly.
    """
    text = (raw or "").strip().replace("$", "").replace(",", "").replace("_", "")
    if not text:
        raise SnapshotError(f"row {row}: {field} is empty")
    try:
        value = float(text)
    except ValueError:
        raise SnapshotError(f"row {row}: {field}={raw!r} is not a dollar figure") from None
    if value != int(value):
        raise SnapshotError(f"row {row}: {field}={raw!r} has fractional dollars")
    return int(value)


def _parse_date(raw: str | None, *, field: str, row: int) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise SnapshotError(f"row {row}: {field}={raw!r} is not an ISO date") from None


def _read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.exists():
        raise SnapshotError(f"missing required file: {path.name}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = required - columns
        if missing:
            raise SnapshotError(f"{path.name} is missing columns: {sorted(missing)}")
        return list(reader)


def load_snapshot(conn: sqlite3.Connection, directory: str | Path) -> SnapshotMeta:
    """Load a snapshot directory into ``conn``. Returns its metadata.

    Idempotent: reloading the same snapshot_id replaces it wholesale rather
    than accumulating duplicates.
    """
    directory = Path(directory)
    meta_path = directory / "snapshot.yaml"
    if not meta_path.exists():
        raise SnapshotError(f"{directory} has no snapshot.yaml")

    raw_meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    for key in ("snapshot_id", "as_of_date", "season", "source"):
        if key not in raw_meta:
            raise SnapshotError(f"snapshot.yaml is missing {key!r}")
    meta = SnapshotMeta(
        snapshot_id=str(raw_meta["snapshot_id"]),
        as_of_date=str(raw_meta["as_of_date"]),
        season=str(raw_meta["season"]),
        source=str(raw_meta["source"]),
        notes=str(raw_meta.get("notes", "")),
    )

    conn.execute("DELETE FROM snapshots WHERE snapshot_id = ?", (meta.snapshot_id,))
    conn.execute(
        "INSERT INTO snapshots (snapshot_id, as_of_date, season, source, loaded_at, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            meta.snapshot_id,
            meta.as_of_date,
            meta.season,
            meta.source,
            datetime.now(UTC).isoformat(timespec="seconds"),
            meta.notes,
        ),
    )

    _load_teams(conn, directory, meta)
    _load_players(conn, directory, meta)
    _load_contracts(conn, directory, meta)
    _load_optional_draft_picks(conn, directory, meta)
    _load_optional_trade_exceptions(conn, directory, meta)
    conn.commit()
    return meta


def _load_teams(conn: sqlite3.Connection, directory: Path, meta: SnapshotMeta) -> None:
    rows = _read_csv(
        directory / "teams.csv",
        {"team_id", "name", "city", "conference", "division"},
    )
    conn.executemany(
        "INSERT INTO teams (team_id, snapshot_id, name, city, conference, division)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                r["team_id"].strip(),
                meta.snapshot_id,
                r["name"].strip(),
                r["city"].strip(),
                r["conference"].strip(),
                r["division"].strip(),
            )
            for r in rows
        ],
    )


def _load_players(conn: sqlite3.Connection, directory: Path, meta: SnapshotMeta) -> None:
    rows = _read_csv(directory / "players.csv", {"player_id", "name"})
    conn.executemany(
        "INSERT INTO players (player_id, snapshot_id, name, position, birth_date)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            (
                r["player_id"].strip(),
                meta.snapshot_id,
                r["name"].strip(),
                (r.get("position") or "").strip() or None,
                _parse_date(r.get("birth_date"), field="birth_date", row=i),
            )
            for i, r in enumerate(rows, start=2)
        ],
    )


def _load_contracts(conn: sqlite3.Connection, directory: Path, meta: SnapshotMeta) -> None:
    rows = _read_csv(
        directory / "contracts.csv",
        {"player_id", "team_id", "season", "salary"},
    )
    payload = []
    for i, r in enumerate(rows, start=2):
        salary = parse_money(r["salary"], field="salary", row=i)
        guaranteed_raw = (r.get("guaranteed") or "").strip()
        guaranteed = (
            parse_money(guaranteed_raw, field="guaranteed", row=i) if guaranteed_raw else salary
        )
        match_raw = (r.get("outgoing_match_value") or "").strip()
        previous_raw = (r.get("previous_salary") or "").strip()
        # An absent column means the snapshot has no BYC information, which is
        # 'unknown' — not 'not_re_signed'. The validator turns that into an
        # UNDETERMINED verdict, which is the loud, correct outcome.
        re_sign = (r.get("re_sign_status") or "").strip() or "unknown"
        if re_sign not in {"not_re_signed", "re_signed_bird", "unknown"}:
            raise SnapshotError(f"row {i}: re_sign_status={re_sign!r} is not a valid status")
        payload.append(
            (
                meta.snapshot_id,
                r["player_id"].strip(),
                r["team_id"].strip(),
                r["season"].strip(),
                salary,
                guaranteed,
                (r.get("contract_type") or "standard").strip() or "standard",
                _parse_date(r.get("signed_on"), field="signed_on", row=i),
                _parse_date(
                    r.get("acquired_via_trade_on"), field="acquired_via_trade_on", row=i
                ),
                _parse_date(
                    r.get("trade_restricted_until"), field="trade_restricted_until", row=i
                ),
                1 if (r.get("no_trade_clause") or "").strip() in {"1", "true", "True"} else 0,
                parse_money(match_raw, field="outgoing_match_value", row=i) if match_raw else None,
                re_sign,
                parse_money(previous_raw, field="previous_salary", row=i)
                if previous_raw
                else None,
            )
        )
    conn.executemany(
        "INSERT INTO contracts (snapshot_id, player_id, team_id, season, salary, guaranteed,"
        " contract_type, signed_on, acquired_via_trade_on, trade_restricted_until,"
        " no_trade_clause, outgoing_match_value, re_sign_status, previous_salary)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        payload,
    )


def _load_optional_draft_picks(
    conn: sqlite3.Connection, directory: Path, meta: SnapshotMeta
) -> None:
    path = directory / "draft_picks.csv"
    if not path.exists():
        return
    rows = _read_csv(path, {"origin_team", "owner_team", "draft_year", "round"})
    conn.executemany(
        "INSERT INTO draft_picks (snapshot_id, origin_team, owner_team, draft_year, round,"
        " protection) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                meta.snapshot_id,
                r["origin_team"].strip(),
                r["owner_team"].strip(),
                int(r["draft_year"]),
                int(r["round"]),
                (r.get("protection") or "").strip() or None,
            )
            for r in rows
        ],
    )


def _load_optional_trade_exceptions(
    conn: sqlite3.Connection, directory: Path, meta: SnapshotMeta
) -> None:
    path = directory / "trade_exceptions.csv"
    if not path.exists():
        return
    rows = _read_csv(path, {"team_id", "label", "amount", "created_season"})
    conn.executemany(
        "INSERT INTO trade_exceptions (snapshot_id, team_id, label, amount, created_season,"
        " expires_on, from_sign_and_trade) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                meta.snapshot_id,
                r["team_id"].strip(),
                r["label"].strip(),
                parse_money(r["amount"], field="amount", row=i),
                r["created_season"].strip(),
                _parse_date(r.get("expires_on"), field="expires_on", row=i),
                1
                if (r.get("from_sign_and_trade") or "").strip() in {"1", "true", "True"}
                else 0,
            )
            for i, r in enumerate(rows, start=2)
        ],
    )


# --------------------------------------------------------------------------
# SQLite -> rules-engine inputs
# --------------------------------------------------------------------------


def _team_salary(
    conn: sqlite3.Connection, snapshot_id: str, team_id: str, season: str
) -> tuple[int, int, int]:
    """Returns (team_salary, standard_roster_count, dead_money)."""
    row = conn.execute(
        "SELECT COALESCE(SUM(salary), 0) AS total, COUNT(*) AS n FROM contracts"
        " WHERE snapshot_id = ? AND team_id = ? AND season = ?"
        "   AND contract_type = 'standard'",
        (snapshot_id, team_id, season),
    ).fetchone()
    dead = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM dead_money"
        " WHERE snapshot_id = ? AND team_id = ? AND season = ?",
        (snapshot_id, team_id, season),
    ).fetchone()
    return int(row["total"]) + int(dead["total"]), int(row["n"]), int(dead["total"])


def team_salary_state(
    conn: sqlite3.Connection, snapshot_id: str, team_id: str, season: str
) -> TeamSalaryState:
    """Build the cap-position view of a team from the world database."""
    total, roster, dead = _team_salary(conn, snapshot_id, team_id, season)
    two_way = conn.execute(
        "SELECT COUNT(*) AS n FROM contracts WHERE snapshot_id = ? AND team_id = ?"
        " AND season = ? AND contract_type = 'two_way'",
        (snapshot_id, team_id, season),
    ).fetchone()
    return TeamSalaryState(
        team_id=team_id,
        season=season,
        team_salary=total,
        roster_count=roster,
        dead_money=dead,
        two_way_count=int(two_way["n"]),
    )


def player_asset(
    conn: sqlite3.Connection,
    snapshot_id: str,
    player_id: str,
    season: str,
    to_team: str,
) -> PlayerAsset:
    """Build a tradeable player from stored contract data.

    Carries the BYC preconditions through, so a snapshot that never recorded
    re-sign status produces UNDETERMINED verdicts rather than quietly
    confident ones.
    """
    row = conn.execute(
        "SELECT c.*, p.name FROM contracts c JOIN players p"
        "  ON p.snapshot_id = c.snapshot_id AND p.player_id = c.player_id"
        " WHERE c.snapshot_id = ? AND c.player_id = ? AND c.season = ?",
        (snapshot_id, player_id, season),
    ).fetchone()
    if row is None:
        raise SnapshotError(f"no {season} contract for {player_id!r} in {snapshot_id!r}")

    return PlayerAsset(
        player_id=player_id,
        name=row["name"],
        salary=int(row["salary"]),
        from_team=row["team_id"],
        to_team=to_team,
        outgoing_match_value=row["outgoing_match_value"],
        acquired_via_trade_on=_as_date(row["acquired_via_trade_on"]),
        signed_on=_as_date(row["signed_on"]),
        trade_restricted_until=_as_date(row["trade_restricted_until"]),
        no_trade_clause=bool(row["no_trade_clause"]),
        re_sign_status=ReSignStatus(row["re_sign_status"]),
        previous_salary=row["previous_salary"],
    )


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def team_trade_state(
    conn: sqlite3.Connection, snapshot_id: str, team_id: str, season: str
) -> TeamTradeState:
    """Build the trade-validator view of a team from the world database."""
    total, roster, _dead = _team_salary(conn, snapshot_id, team_id, season)

    tpes = tuple(
        TradeException(
            amount=int(r["amount"]),
            created_season=r["created_season"],
            label=r["label"],
            from_sign_and_trade=bool(r["from_sign_and_trade"]),
        )
        for r in conn.execute(
            "SELECT amount, created_season, label, from_sign_and_trade FROM trade_exceptions"
            " WHERE snapshot_id = ? AND team_id = ?",
            (snapshot_id, team_id),
        )
    )

    picks = tuple(
        (int(r["draft_year"]), int(r["n"]))
        for r in conn.execute(
            "SELECT draft_year, COUNT(*) AS n FROM draft_picks"
            " WHERE snapshot_id = ? AND owner_team = ? AND round = 1"
            " GROUP BY draft_year ORDER BY draft_year",
            (snapshot_id, team_id),
        )
    )

    return TeamTradeState(
        team_id=team_id,
        team_salary=total,
        roster_count=roster,
        trade_exceptions=tpes,
        first_round_picks=picks,
    )
