"""Snapshot loading, and the bridge from stored data into the rules engine.

Runs against a real ingested snapshot (`bbref-2024-25`), not a hand-built one.
Several tests here exist to pin the *limitations* of that data rather than its
strengths — an ingest that quietly produces plausible-but-wrong roster counts
or BYC assumptions is more dangerous than one that has none.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from mironba.data.db import connect, initialize
from mironba.data.loader import (
    SnapshotError,
    load_snapshot,
    parse_money,
    player_asset,
    team_salary_state,
    team_trade_state,
)
from mironba.rules.cap import ApronTier
from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    ReSignStatus,
    TeamTradeState,
    Trade,
    Verdict,
    VerdictUndetermined,
    validate_trade,
)

SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[1]
    / "mironba"
    / "data"
    / "snapshots"
    / "bbref-2024-25"
)

#: The ingested tables are not redistributed, so a fresh clone does not have
#: them (see "Reproducing the data snapshot" in README.md). Tests that need
#: real salaries skip with the rebuild command in the reason — a clean
#: checkout must not look broken. `sources.csv` *is* committed, so the
#: provenance test below keeps running either way.
requires_ingested_data = pytest.mark.skipif(
    not (SNAPSHOT_DIR / "contracts.csv").exists(),
    reason=(
        "ingested tables absent; rebuild with "
        "`python -m mironba.data.ingest.build --seasons 2024-25`"
    ),
)


@pytest.fixture
def empty_db():
    """An initialised, empty database that is always closed.

    Leaked connections surface as ResourceWarnings, and this suite runs with
    ``filterwarnings = ["error"]`` — an unclosed handle here fails whichever
    unrelated test happens to be running when the collector gets to it.
    """
    conn = connect()
    initialize(conn)
    yield conn
    conn.close()


@pytest.fixture
def loaded_db(empty_db):
    if not (SNAPSHOT_DIR / "contracts.csv").exists():
        pytest.skip(
            "ingested tables absent; rebuild with "
            "`python -m mironba.data.ingest.build --seasons 2024-25`"
        )
    meta = load_snapshot(empty_db, SNAPSHOT_DIR)
    yield empty_db, meta


class TestParseMoney:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12345678", 12_345_678),
            ("12,345,678", 12_345_678),
            ("$12,345,678", 12_345_678),
            ("12345678.00", 12_345_678),
            ("  5000000  ", 5_000_000),
        ],
    )
    def test_accepts_common_formats(self, raw, expected):
        assert parse_money(raw, field="salary", row=2) == expected

    @pytest.mark.parametrize("raw", ["", "  ", "abc", "1.5", "12,34x"])
    def test_rejects_anything_else(self, raw):
        """A silently-zeroed salary yields a confidently wrong trade verdict."""
        with pytest.raises(SnapshotError):
            parse_money(raw, field="salary", row=2)


class TestSnapshotLoading:
    def test_loads_all_thirty_teams(self, loaded_db):
        conn, meta = loaded_db
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE snapshot_id = ?", (meta.snapshot_id,)
        ).fetchone()
        assert count == 30

    def test_metadata_records_the_source(self, loaded_db):
        conn, meta = loaded_db
        assert meta.season == "2024-25"
        assert meta.source == "basketball-reference.com"
        row = conn.execute(
            "SELECT * FROM snapshots WHERE snapshot_id = ?", (meta.snapshot_id,)
        ).fetchone()
        assert row["loaded_at"]

    def test_every_ingested_table_has_a_source_url_and_date(self):
        """The charter's provenance rule, enforced on ingested data."""
        import csv

        with (SNAPSHOT_DIR / "sources.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        for row in rows:
            assert row["source_url"].startswith("https://"), row
            assert date.fromisoformat(row["retrieved_date"])
        # 30 team salary pages plus one transaction log.
        assert sum(1 for r in rows if r["table"] == "salaries") == 30
        assert sum(1 for r in rows if r["table"] == "transactions") == 1

    def test_reloading_replaces_rather_than_duplicates(self, loaded_db):
        conn, meta = loaded_db
        load_snapshot(conn, SNAPSHOT_DIR)
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM teams WHERE snapshot_id = ?", (meta.snapshot_id,)
        ).fetchone()
        assert count == 30

    def test_missing_metadata_file_raises(self, empty_db, tmp_path):
        with pytest.raises(SnapshotError, match="snapshot.yaml"):
            load_snapshot(empty_db, tmp_path)

    @requires_ingested_data
    def test_missing_column_raises(self, empty_db, tmp_path):
        shutil.copytree(SNAPSHOT_DIR, tmp_path / "snap")
        broken = tmp_path / "snap" / "contracts.csv"
        lines = broken.read_text(encoding="utf-8").splitlines()
        lines[0] = lines[0].replace("salary,", "")
        broken.write_text("\n".join(lines), encoding="utf-8")

        with pytest.raises(SnapshotError, match="missing columns"):
            load_snapshot(empty_db, tmp_path / "snap")


@pytest.mark.parametrize(
    "snapshot", ["bbref-2023-24", "bbref-2024-25", "bbref-2025-26"]
)
def test_provenance_manifest_outlives_the_data(snapshot):
    """Every snapshot keeps its manifest even when the tables are gone.

    The scraped tables are gitignored and the tests that read them skip, so
    nothing else in the suite would notice if a snapshot lost its provenance.
    `sources.csv` and `snapshot.yaml` are the part that must survive a clone:
    without them there is no record of which URLs produced the figures quoted
    in the README, and the rebuild cannot be checked against the original run.
    """
    for name in ("sources.csv", "snapshot.yaml"):
        assert (SNAPSHOT_DIR.parent / snapshot / name).exists(), (
            f"{snapshot}/{name} is missing — it is provenance, not data, "
            "and must be committed"
        )


class TestRealSalaryData:
    def test_known_salaries_survive_the_round_trip(self, loaded_db):
        """Spot-checks against figures visible on the source pages."""
        conn, meta = loaded_db
        for player_id, expected in [
            ("curryst01", 55_761_216),
            ("brownja02", 49_205_800),
            ("tatumja01", 34_848_340),
        ]:
            row = conn.execute(
                "SELECT salary FROM contracts WHERE snapshot_id = ? AND player_id = ?"
                " AND season = ?",
                (meta.snapshot_id, player_id, "2024-25"),
            ).fetchone()
            assert row is not None, player_id
            assert int(row["salary"]) == expected, player_id

    def test_team_salary_places_teams_in_the_right_tiers(self, loaded_db):
        conn, meta = loaded_db
        env = environment_for("2024-25")

        phx = team_salary_state(conn, meta.snapshot_id, "PHX", "2024-25")
        assert phx.tier(env) is ApronTier.SECOND_APRON

        uta = team_salary_state(conn, meta.snapshot_id, "UTA", "2024-25")
        assert uta.tier(env) is ApronTier.OVER_CAP


class TestIngestLimitations:
    """These pin what the data cannot do. They are the useful ones."""

    def test_roster_count_is_not_an_active_roster(self, loaded_db):
        """A season salary table lists everyone who drew a cheque.

        Some teams exceed the 15-man limit outright, so ``roster_count`` from a
        Basketball-Reference snapshot cannot be fed to the roster rules. Pinned
        here so nobody later mistakes it for an active roster and gets
        ROSTER_LIMIT errors they cannot explain.
        """
        conn, meta = loaded_db
        counts = {
            team: team_trade_state(conn, meta.snapshot_id, team, "2024-25").roster_count
            for team in ("BOS", "UTA", "WAS")
        }
        assert max(counts.values()) > 15, counts

    def test_every_player_has_unknown_re_sign_status(self, loaded_db):
        """Basketball-Reference does not publish re-signing status.

        So every trade built from this snapshot is UNDETERMINED for BYC. That
        is the correct answer, and it is why a REALITY fixture cannot be
        derived from this data alone.
        """
        conn, meta = loaded_db
        asset = player_asset(conn, meta.snapshot_id, "tatumja01", "2024-25", "UTA")
        assert asset.re_sign_status is ReSignStatus.UNKNOWN

    def test_a_trade_from_this_snapshot_is_undetermined(self, loaded_db):
        conn, meta = loaded_db
        trade = Trade(
            season="2024-25",
            trade_date=date(2025, 2, 6),
            # Roster counts supplied by hand — see the test above for why the
            # snapshot's own counts cannot be used here.
            teams=(
                TeamTradeState("BOS", team_salary=192_568_625, roster_count=15),
                TeamTradeState("UTA", team_salary=151_232_088, roster_count=15),
            ),
            players=(
                player_asset(conn, meta.snapshot_id, "holidjr01", "2024-25", "UTA"),
                player_asset(conn, meta.snapshot_id, "collijo01", "2024-25", "BOS"),
            ),
        )
        result = validate_trade(trade)
        assert result.verdict is Verdict.UNDETERMINED
        with pytest.raises(VerdictUndetermined):
            _ = result.legal
        assert all(
            f.rule == "BASE_YEAR_COMPENSATION" for f in result.undetermined()
        )

    def test_resolving_byc_by_hand_yields_a_real_verdict(self, loaded_db):
        """The manual step every REALITY fixture will need.

        BOS at $192,568,625 is above the 2024-25 second apron ($188,931,000),
        so it may take back no more than the $30,000,000 it sends. Jrue
        Holiday out, John Collins ($26,580,000) back, clears by $3,420,000.
        """
        conn, meta = loaded_db
        # Both outgoing players need the BYC question answered; a researcher
        # would confirm neither was re-signed and record that on the fixture.
        resolved = tuple(
            replace(
                player_asset(conn, meta.snapshot_id, pid, "2024-25", to_team),
                re_sign_status=ReSignStatus.NOT_RE_SIGNED,
            )
            for pid, to_team in (("holidjr01", "UTA"), ("collijo01", "BOS"))
        )
        trade = Trade(
            season="2024-25",
            trade_date=date(2025, 2, 6),
            teams=(
                TeamTradeState("BOS", team_salary=192_568_625, roster_count=15),
                TeamTradeState("UTA", team_salary=151_232_088, roster_count=15),
            ),
            players=resolved,
        )
        result = validate_trade(trade)
        assert result.verdict is Verdict.APPROVED, result.explain()
        assert result.per_team["BOS"].tier_after is ApronTier.SECOND_APRON
        assert result.per_team["BOS"].match.max_incoming == 30_000_000
