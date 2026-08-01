"""Every partitioned writer must merge, not overwrite.

Three writers in `data/ingest/` had the same defect and they were found one at
a time, a round apart each:

1. `write_game_logs` — found before it did damage.
2. `write_snapshot`'s data tables — found *after* it replaced an eleven-season
   player-stats table with a two-season one. Recovered from git.
3. `write_snapshot`'s `sources.csv` — found only when this test was written,
   after the damage had been committed. Recovered from an earlier commit.

None of them raised. The files still parsed, the loaders still loaded, and the
symptom was a downstream number quietly becoming zero.

So this test enumerates the writers **programmatically**. A fourth writer added
later is covered without anyone remembering to cover it, which is the entire
lesson: fixing one call site is not fixing the class.
"""

from __future__ import annotations

import csv
import inspect
from pathlib import Path

import pytest

from mironba.data.ingest import nba_stats
from mironba.eval import pooled_backtest

#: Writers that take season-partitioned data. Discovered, not listed.
WRITERS = sorted(
    name for name, obj in vars(nba_stats).items()
    if name.startswith("write_") and inspect.isfunction(obj)
)


def _scopes(path: Path, column: str) -> set[str]:
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {row[column] for row in csv.DictReader(handle)}


def test_the_writer_inventory_is_not_empty():
    """If discovery breaks, every test below silently passes on nothing."""
    assert WRITERS, "no writers discovered; the enumeration has broken"
    assert "write_snapshot" in WRITERS
    assert "write_game_logs" in WRITERS


@pytest.mark.parametrize("writer", WRITERS)
def test_every_writer_is_declared_partitioned_or_not(writer):
    """A writer must say which it is, so a new one cannot be ambiguous."""
    assert writer in nba_stats.PARTITIONED or writer in nba_stats.WHOLE_TABLE, (
        f"{writer} is neither in PARTITIONED nor WHOLE_TABLE. A new writer must "
        "declare whether it takes season-partitioned data: if it does and it "
        "opens 'w' with only the current pull, it destroys every other season."
    )


class TestPartitionedWritersMerge:
    """Write partition A, then write partition B, assert A survives."""

    def test_game_logs_merge(self, tmp_path):
        nba_stats.write_game_logs({"A": [{"GAME_ID": "1"}]}, root=tmp_path)
        nba_stats.write_game_logs({"B": [{"GAME_ID": "2"}]}, root=tmp_path)
        scopes = _scopes(tmp_path / "nba-stats" / "game_logs.csv", "season")
        assert scopes == {"A", "B"}, f"writing B lost A: {scopes}"

    def test_snapshot_data_tables_merge(self, tmp_path):
        a = nba_stats.SeasonPull(season="A", players=[{"PLAYER_ID": "1"}],
                                 teams=[{"TEAM_ID": "1"}], retrieved_at="2026-08-01T00:00:00")
        b = nba_stats.SeasonPull(season="B", players=[{"PLAYER_ID": "2"}],
                                 teams=[{"TEAM_ID": "2"}], retrieved_at="2026-08-01T00:00:00")
        nba_stats.write_snapshot([a], root=tmp_path)
        nba_stats.write_snapshot([b], root=tmp_path)
        for table in ("player_seasons.csv", "team_seasons.csv"):
            scopes = _scopes(tmp_path / "nba-stats" / table, "season")
            assert scopes == {"A", "B"}, f"{table}: writing B lost A ({scopes})"

    def test_snapshot_provenance_merges(self, tmp_path):
        """The one found last, and the one that mattered most.

        Losing a data row loses a number. Losing a provenance row loses the
        answer to "where did this season come from", which this project
        refuses to leave unanswered.
        """
        a = nba_stats.SeasonPull(season="A", players=[{"PLAYER_ID": "1"}],
                                 teams=[{"TEAM_ID": "1"}], retrieved_at="2026-08-01T00:00:00")
        b = nba_stats.SeasonPull(season="B", players=[{"PLAYER_ID": "2"}],
                                 teams=[{"TEAM_ID": "2"}], retrieved_at="2026-08-01T00:00:00")
        nba_stats.write_snapshot([a], root=tmp_path)
        nba_stats.write_snapshot([b], root=tmp_path)
        scopes = _scopes(tmp_path / "nba-stats" / "sources.csv", "scope")
        assert scopes == {"A", "B"}, f"provenance for A was destroyed: {scopes}"

    def test_rewriting_the_same_partition_replaces_rather_than_duplicates(self, tmp_path):
        """Merging must not turn a re-ingest into a duplicate."""
        first = nba_stats.SeasonPull(season="A", players=[{"PLAYER_ID": "1"}],
                                     teams=[{"TEAM_ID": "1"}], retrieved_at="2026-08-01T00:00:00")
        nba_stats.write_snapshot([first], root=tmp_path)
        nba_stats.write_snapshot([first], root=tmp_path)
        path = tmp_path / "nba-stats" / "player_seasons.csv"
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1, f"re-ingest duplicated rows: {len(rows)}"


class TestResultWritersMergeToo:
    """A results writer is a partitioned writer.

    ``bench-pooled-10season`` accumulates one row per season over a 2.5-hour
    run. The first version wrote once on completion, so a crash at season nine
    lost seasons one to eight - a different failure from the ingest writers but
    the same lesson, and the reason this class enumerates beyond data/ingest.
    """

    def test_a_second_season_does_not_erase_the_first(self, tmp_path):
        path = tmp_path / "results.csv"
        pooled_backtest.write_season(
            {"season": "A", "proposed": 1, "pairs": 1, "actual": 1,
             "matched": 1, "hits": 1}, path)
        pooled_backtest.write_season(
            {"season": "B", "proposed": 2, "pairs": 2, "actual": 2,
             "matched": 2, "hits": 2}, path)
        assert set(pooled_backtest.read_seasons(path)) == {"A", "B"}

    def test_re_running_a_season_replaces_rather_than_duplicates(self, tmp_path):
        path = tmp_path / "results.csv"
        for proposed in (1, 9):
            pooled_backtest.write_season(
                {"season": "A", "proposed": proposed, "pairs": 1, "actual": 1,
                 "matched": 1, "hits": 1}, path)
        stored = pooled_backtest.read_seasons(path)
        assert len(stored) == 1
        assert stored["A"]["proposed"] == "9"

    def test_results_survive_an_interrupted_run(self, tmp_path):
        """The property that matters: work done is work kept."""
        path = tmp_path / "results.csv"
        for season in ("A", "B", "C"):
            pooled_backtest.write_season(
                {"season": season, "proposed": 1, "pairs": 1, "actual": 1,
                 "matched": 1, "hits": 1}, path)
        # Simulate a crash: nothing further is written.
        assert set(pooled_backtest.read_seasons(path)) == {"A", "B", "C"}


class TestTheRealSnapshotIsIntact:
    """The shipped snapshot, which two of these bugs damaged."""

    def test_provenance_covers_every_ingested_season(self):
        root = Path(nba_stats.SNAPSHOT_ROOT) / "nba-stats"
        data = _scopes(root / "player_seasons.csv", "season")
        prov = _scopes(root / "sources.csv", "scope")
        if not data:
            pytest.skip("snapshot not present")
        missing = sorted(data - prov)
        assert not missing, f"seasons with data but no provenance: {missing}"
