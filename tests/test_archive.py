"""The standing RSS archive: append-only, every day accounted for, gaps honest."""

from __future__ import annotations

import csv
from datetime import date

from mironba.data.ingest.archive import (
    COVERED,
    MISSING,
    UNRECOVERABLE,
    _marker_row,
    coverage,
    day_status,
    is_marker,
    partition_path,
    read_partition,
    recover,
    window,
    write_archive_rows,
)


def _row(url, published, feed="espn-nba"):
    return {"feed": feed, "url": url, "title": "t", "summary": "s",
            "published_at": published, "fetched_at": "2026-08-02T09:00:00+00:00"}


def _span(root):
    parts = sorted(root.glob("*.csv"))
    return (parts[0].stem, parts[-1].stem, len(parts)) if parts else None


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
        assert _span(tmp_path) == ("2026-07-30", "2026-07-31", 2)

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
        row = read_partition(date(2026, 7, 30), tmp_path)[0]
        assert row["published_at"].startswith("2026-07-30")
        assert row["fetched_at"].startswith("2026-08-02")


class TestAbsentAndEmptyAreDifferent:
    def test_a_day_with_no_file_is_missing(self, tmp_path):
        assert day_status(date(2026, 8, 1), tmp_path) == MISSING

    def test_a_poll_that_returned_nothing_still_covers_its_day(self, tmp_path):
        marker = _marker_row("__poll__", date(2026, 8, 1),
                             "poll ran: 0 items", "2026-08-01T09:00:00+00:00")
        write_archive_rows([marker], root=tmp_path)
        assert day_status(date(2026, 8, 1), tmp_path) == COVERED

    def test_markers_never_reach_a_reader_as_articles(self, tmp_path):
        marker = _marker_row("__poll__", date(2026, 8, 1), "poll", "s1")
        write_archive_rows([marker, _row("http://a", "2026-08-01T08:00:00+00:00")],
                           root=tmp_path)
        rows = read_partition(date(2026, 8, 1), tmp_path)
        articles = [r for r in rows if not is_marker(r)]
        assert len(rows) == 2 and len(articles) == 1

    def test_two_polls_one_day_write_two_markers(self, tmp_path):
        write_archive_rows([_marker_row("__poll__", date(2026, 8, 1), "am", "s1")],
                           root=tmp_path)
        write_archive_rows([_marker_row("__poll__", date(2026, 8, 1), "pm", "s2")],
                           root=tmp_path)
        assert len(read_partition(date(2026, 8, 1), tmp_path)) == 2


class TestGapDetection:
    def test_every_missing_day_is_listed_and_the_longest_gap_measured(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-08-01T08:00:00+00:00")],
                           root=tmp_path)
        write_archive_rows([_row("http://b", "2026-08-05T08:00:00+00:00")],
                           root=tmp_path)
        report = coverage(tmp_path, today=date(2026, 8, 6))
        assert report["missing"] == [date(2026, 8, 2), date(2026, 8, 3),
                                     date(2026, 8, 4), date(2026, 8, 6)]
        assert report["longest_gap"] == 3
        assert report["longest_gap_range"] == (date(2026, 8, 2), date(2026, 8, 4))


class TestHonestRecovery:
    def test_a_gap_within_measured_reach_is_recovered(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-08-01T08:00:00+00:00")],
                           root=tmp_path)
        result = recover({"espn-nba": 3}, tmp_path, today=date(2026, 8, 3),
                         stamp="s")
        assert date(2026, 8, 2) in result["recovered"]
        assert day_status(date(2026, 8, 2), tmp_path) == COVERED

    def test_a_gap_beyond_reach_is_unrecoverable_never_success(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-08-01T08:00:00+00:00")],
                           root=tmp_path)
        result = recover({"espn-nba": 2}, tmp_path, today=date(2026, 8, 10),
                         stamp="s")
        gap_day = date(2026, 8, 3)
        assert gap_day in result["unrecoverable"]
        assert gap_day not in result["recovered"]
        assert day_status(gap_day, tmp_path) == UNRECOVERABLE

    def test_an_unrecoverable_day_can_never_become_recovered(self, tmp_path):
        """The one-way door: once marked beyond reach, a later run with a
        longer reach must not flip it to recovered - the items are gone and a
        recovery claim would be a success report over an absence."""
        write_archive_rows([_row("http://a", "2026-08-01T08:00:00+00:00")],
                           root=tmp_path)
        recover({"espn-nba": 1}, tmp_path, today=date(2026, 8, 10), stamp="s1")
        gap_day = date(2026, 8, 4)
        assert day_status(gap_day, tmp_path) == UNRECOVERABLE
        second = recover({"espn-nba": 400}, tmp_path,
                         today=date(2026, 8, 10), stamp="s2")
        assert gap_day not in second["recovered"]
        assert day_status(gap_day, tmp_path) == UNRECOVERABLE


class _Scenario:
    id = "t-window"
    subjects = ("curryst01", "GSW")
    freeze = date(2026, 8, 5)


class TestScenarioWindow:
    def test_coverage_is_reported_before_anything_returns(self, tmp_path):
        write_archive_rows([
            _row("http://hit", "2026-08-04T08:00:00+00:00"),
            _marker_row("__poll__", date(2026, 8, 5), "poll", "s"),
        ], root=tmp_path)
        report = window(_Scenario(), lookback_days=5, root=tmp_path)
        assert report["requested"] == 5
        assert report["covered"] == 2
        assert report["gaps"], "the gap must be stated, not skipped"
        lo, hi = report["gaps"][0]
        assert (lo, hi) == (date(2026, 8, 1), date(2026, 8, 3))

    def test_days_before_the_archive_are_their_own_kind_of_gap(self, tmp_path):
        write_archive_rows([_row("http://a", "2026-08-04T08:00:00+00:00")],
                           root=tmp_path)
        report = window(_Scenario(), lookback_days=5, root=tmp_path)
        assert report["statuses"][date(2026, 8, 1)] == "BEFORE-ARCHIVE"

    def test_subject_matching_and_marker_filtering(self, tmp_path):
        write_archive_rows([
            {"feed": "espn-nba", "url": "http://m", "title": "GSW news",
             "summary": "", "published_at": "2026-08-04T08:00:00+00:00",
             "fetched_at": "f"},
            {"feed": "espn-nba", "url": "http://x", "title": "other team",
             "summary": "", "published_at": "2026-08-04T09:00:00+00:00",
             "fetched_at": "f"},
            _marker_row("__poll__", date(2026, 8, 4), "poll", "s"),
        ], root=tmp_path)
        report = window(_Scenario(), lookback_days=2, root=tmp_path)
        assert len(report["items"]) == 2
        assert [r["url"] for r in report["matching"]] == ["http://m"]


class TestHealthIsVisibleUnasked:
    def _seed(self, tmp_path, day="2026-08-01"):
        write_archive_rows([
            _row("http://a", f"{day}T08:00:00+00:00"),
            _marker_row("__poll__", date.fromisoformat(day), "poll ran",
                        f"{day}T09:00:00+00:00"),
        ], root=tmp_path)

    def test_staleness_is_loud_with_the_last_poll_date(self, tmp_path):
        from mironba.data.ingest.archive import announce

        self._seed(tmp_path, "2026-08-01")
        text = announce(tmp_path, today=date(2026, 8, 5))
        assert "ARCHIVE STALE" in text
        assert "4 day(s) old" in text
        assert "2026-08-01T09:00:00+00:00" in text, "last poll must be named"

    def test_a_fresh_archive_is_not_shouted_about(self, tmp_path):
        from mironba.data.ingest.archive import announce

        self._seed(tmp_path, "2026-08-01")
        text = announce(tmp_path, today=date(2026, 8, 3))
        assert "STALE" not in text
        assert text.startswith("archive health:")

    def test_an_archive_that_never_ran_says_so(self, tmp_path):
        from mironba.data.ingest.archive import announce

        assert "ARCHIVE EMPTY" in announce(tmp_path, today=date(2026, 8, 3))

    def test_coverage_appears_on_every_run_not_on_request(self, tmp_path, capsys):
        from mironba.data.ingest.archive import main

        self._seed(tmp_path, "2026-08-01")
        main(["--coverage", "--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert out.splitlines()[0].startswith(("archive health:", "!! ARCHIVE"))

    def test_unrecoverable_days_group_into_ranges(self, tmp_path):
        from mironba.data.ingest.archive import health

        self._seed(tmp_path, "2026-08-01")
        recover({"espn-nba": 1}, tmp_path, today=date(2026, 8, 10), stamp="s")
        h = health(tmp_path, today=date(2026, 8, 10), query_scheduler=False)
        assert len(h["unrecoverable_ranges"]) == 1
        lo, hi = h["unrecoverable_ranges"][0]
        assert (lo, hi) == (date(2026, 8, 2), date(2026, 8, 8))

    def test_last_poll_is_the_newest_marker(self, tmp_path):
        from mironba.data.ingest.archive import last_poll_stamp

        write_archive_rows([
            _marker_row("__poll__", date(2026, 8, 1), "am", "2026-08-01T09:00:00+00:00"),
            _marker_row("__poll__", date(2026, 8, 1), "pm", "2026-08-01T21:00:00+00:00"),
        ], root=tmp_path)
        assert last_poll_stamp(tmp_path) == "2026-08-01T21:00:00+00:00"
