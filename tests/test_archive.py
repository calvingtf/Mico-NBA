"""The standing RSS archive: append-only, one writer, partitioned by date."""

from __future__ import annotations

import csv

from mironba.data.ingest.archive import (
    archive_span,
    partition_path,
    write_archive_rows,
)


def _row(url, published, feed="espn-nba"):
    return {"feed": feed, "url": url, "title": "t", "summary": "s",
            "published_at": published, "fetched_at": "2026-08-02T09:00:00+00:00"}


class TestAppendOnly:
    def test_partitions_are_by_published_date_not_fetch_date(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-07-30T08:00:00+00:00")],
                           root=tmp_path)
        assert (tmp_path / "2026-07-30.csv").is_file()

    def test_a_second_poll_does_not_erase_the_first(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-07-30T08:00:00+00:00")],
                           root=tmp_path)
        write_archive_rows([_row("http://b", "2026-07-31T08:00:00+00:00")],
                           root=tmp_path)
        assert archive_span(tmp_path) == ("2026-07-30", "2026-07-31", 2, 2)

    def test_repolling_the_same_item_appends_nothing(self, tmp_path):
        row = _row("http://a", "2026-07-30T08:00:00+00:00")
        first = write_archive_rows([row], root=tmp_path)
        second = write_archive_rows([row], root=tmp_path)
        assert (first["appended"], second["appended"]) == (1, 0)
        assert second["duplicate"] == 1
        path = partition_path(row["published_at"], tmp_path)
        with path.open(encoding="utf-8", newline="") as handle:
            assert len(list(csv.DictReader(handle))) == 1

    def test_published_and_fetched_are_recorded_separately(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-07-30T08:00:00+00:00")],
                           root=tmp_path)
        path = partition_path("2026-07-30", tmp_path)
        with path.open(encoding="utf-8", newline="") as handle:
            row = next(csv.DictReader(handle))
        assert row["published_at"].startswith("2026-07-30")
        assert row["fetched_at"].startswith("2026-08-02")
