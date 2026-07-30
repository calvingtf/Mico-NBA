"""Ingest parsing, offline.

No network. The parsers are exercised on markup fragments in the shapes
Basketball-Reference actually emits, including the two that bit us: tables
hidden inside HTML comments, and multi-team trades written as one
semicolon-separated sentence.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from mironba.data.ingest import bbref
from mironba.data.ingest.cache import fetch

SALARY_PAGE = """
<div id="all_salaries2">
<!--
<table id="salaries2">
<tr><th>Rk</th><th>Player</th><th>Salary</th></tr>
<tr><td data-stat="ranker">1</td>
    <td data-stat="player"><a href="/players/h/holidjr01.html">Jrue Holiday</a></td>
    <td data-stat="salary">$36,861,707</td></tr>
<tr><td data-stat="ranker">2</td>
    <td data-stat="player"><a href="/players/p/porzikr01.html">Kristaps Porzingis</a></td>
    <td data-stat="salary">$36,016,200</td></tr>
</table>
-->
</div>
"""

TRANSACTIONS_PAGE = """
<li><span>February 8, 2024</span>
<p>The <a href="/teams/BOS/2024.html">Boston Celtics</a> traded
   <a href="/players/h/holidjr01.html">Jrue Holiday</a> to the
   <a href="/teams/POR/2024.html">Portland Trail Blazers</a> for
   <a href="/players/b/brogdma01.html">Malcolm Brogdon</a>.</p>
<p>The <a href="/teams/UTA/2024.html">Utah Jazz</a> signed
   <a href="/players/c/clarkjo01.html">Jordan Clarkson</a>.</p>
</li>
"""


class TestSalaryParsing:
    def test_reads_a_comment_hidden_table(self):
        rows = bbref.parse_team_salaries(SALARY_PAGE, "BOS", "2023-24")
        assert [(r.player_id, r.salary) for r in rows] == [
            ("holidjr01", 36_861_707),
            ("porzikr01", 36_016_200),
        ]

    def test_maps_team_codes_to_project_codes(self):
        rows = bbref.parse_team_salaries(SALARY_PAGE, "BRK", "2023-24")
        assert {r.team_id for r in rows} == {"BKN"}
        assert bbref.TEAM_CODE["CHO"] == "CHA"
        assert bbref.TEAM_CODE["PHO"] == "PHX"

    def test_missing_table_returns_empty_not_a_guess(self):
        """The caller treats this as a missing source and skips the season."""
        assert bbref.parse_team_salaries("<html></html>", "BOS", "2023-24") == []

    def test_all_thirty_codes_map(self):
        assert len(bbref.BBREF_CODES) == 30
        assert len(set(bbref.TEAM_CODE.values())) == 30


class TestTransactionParsing:
    def test_splits_a_day_into_separate_transactions(self):
        out = bbref.parse_transactions(TRANSACTIONS_PAGE, "2023-24")
        assert len(out) == 2
        assert all(t.date == date(2024, 2, 8) for t in out)
        assert [t.is_trade for t in out] == [True, False]

    def test_marks_player_ids_inline(self):
        """Name matching is unreliable; the anchors are not."""
        trade = bbref.parse_transactions(TRANSACTIONS_PAGE, "2023-24")[0]
        assert "Jrue Holiday{{holidjr01}}" in trade.marked_text
        assert "Malcolm Brogdon{{brogdma01}}" in trade.marked_text
        assert "{{" not in trade.text

    def test_collects_teams_and_players(self):
        trade = bbref.parse_transactions(TRANSACTIONS_PAGE, "2023-24")[0]
        assert set(trade.team_ids) == {"BOS", "POR"}
        assert set(trade.player_ids) == {"holidjr01", "brogdma01"}


class TestSeasonKeys:
    @pytest.mark.parametrize(
        ("season", "year"), [("2023-24", 2024), ("2024-25", 2025), ("2025-26", 2026)]
    )
    def test_season_end_year(self, season, year):
        assert bbref.season_end_year(season) == year
        assert f"/{year}.html" in bbref.team_season_url("BOS", season)
        assert f"NBA_{year}_transactions" in bbref.transactions_url(season)


class TestCache:
    def test_second_fetch_comes_from_disk_and_keeps_provenance(self, tmp_path):
        """A re-run must not re-fetch, and must not lose where the bytes came from."""
        url = "https://example.invalid/page.html"
        body = tmp_path / "seed.html"
        body.write_text("<html>cached</html>", encoding="utf-8")

        # Seed the cache by hand so the test needs no network at all.
        from mironba.data.ingest.cache import _paths

        body_path, meta_path = _paths(url, tmp_path)
        body_path.write_text("<html>cached</html>", encoding="utf-8")
        meta_path.write_text(
            json.dumps({"url": url, "retrieved_at": "2026-07-30T00:00:00+00:00",
                        "status": 200, "bytes": 19}),
            encoding="utf-8",
        )

        result = fetch(url, tmp_path)
        assert result.from_cache is True
        assert result.text == "<html>cached</html>"
        assert result.retrieved_date == "2026-07-30"
