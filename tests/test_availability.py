"""Availability: as-of, display-only, uncertain rows say so."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from mironba.world.availability import (
    AVAILABLE,
    CONTEXT_MARKER,
    NO_LOG_ROW,
    TRADED_IN,
    UNAVAILABLE,
    Appearance,
    availability,
    render_availability,
    team_last_games,
)

ROOT = Path(__file__).resolve().parents[1] / "mironba"


def _logs():
    """Team AAA plays 12 games, days 1..12 of June. Player One plays the last
    three; Player Two plays none; Player Three plays for BBB only."""
    logs = []
    for day in range(1, 13):
        d = date(2026, 6, day)
        logs.append(Appearance("Anchor Man", "AAA", d))  # keeps the schedule
        if day == 1 or day >= 10:
            logs.append(Appearance("Player One", "AAA", d))
    logs.append(Appearance("Player Three", "BBB", date(2026, 6, 2)))
    return logs


class TestAsOf:
    def test_the_window_is_strictly_before_the_date(self):
        games = team_last_games("AAA", date(2026, 6, 12), _logs(), n=10)
        assert max(games) == date(2026, 6, 11), "a game ON the date leaked in"

    def test_the_same_question_at_two_dates_gets_two_answers(self):
        roster = {"one01": "Player One"}
        early, _ = availability("AAA", date(2026, 6, 9), roster, _logs(), n=5)
        late, _ = availability("AAA", date(2026, 6, 13), roster, _logs(), n=5)
        assert early[0].status == UNAVAILABLE
        assert late[0].status == AVAILABLE and late[0].appearances == 3

    def test_the_module_cannot_read_a_clock(self):
        src = (ROOT / "world" / "availability.py").read_text(encoding="utf-8")
        for banned in ("today()", "datetime.now", "time.time"):
            assert banned not in src, f"as-of module reads the clock: {banned}"


class TestUncertaintyIsLabelled:
    def test_zero_appearances_is_unavailable(self):
        rows, _ = availability("AAA", date(2026, 6, 13),
                               {"anchor01": "Anchor Man", "two01": "Player Two"},
                               _logs(), n=5)
        by = {r.player_id: r for r in rows}
        assert by["anchor01"].status == AVAILABLE
        assert by["two01"].status == NO_LOG_ROW

    def test_a_recent_arrival_is_flagged_not_called_unavailable(self):
        rows, _ = availability("AAA", date(2026, 6, 13),
                               {"three01": "Player Three"}, _logs(), n=5)
        assert rows[0].status == TRADED_IN
        assert "window predates" in rows[0].note

    def test_the_join_reports_its_hit_rate(self):
        rows, join = availability("AAA", date(2026, 6, 13),
                                  {"one01": "Player One", "ghost01": "No Such Guy"},
                                  _logs(), n=5)
        assert join.total == 2 and join.matched == 1
        assert "50.0%" in join.report()


class TestDisplayOnly:
    def test_the_render_carries_the_context_marker(self):
        text = render_availability("AAA", date(2026, 6, 13),
                                   {"one01": "Player One"}, _logs(), n=5)
        assert CONTEXT_MARKER in text
        assert "not a planner input" in CONTEXT_MARKER

    def test_no_sim_path_can_read_availability(self):
        """The fence, NARROWED rather than removed: availability stays out of
        the planner (sim/), the agents, the value model (models/) and the
        rules - nothing about its effect on GM behaviour or player value has
        been validated. The ranker is the ONE permitted consumer outside the
        display surface: it is exactly the pre-deadline signal a ranker is
        for, and ranker outputs feed no simulation. Widening this allowlist
        requires widening this test, deliberately."""
        allowed = {"player_ranker.py"}
        offenders = []
        for package in ("sim", "agents", "models", "rules", "eval"):
            for path in (ROOT / package).rglob("*.py"):
                if package == "eval" and path.name in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                if "world.availability" in text or "import availability" in text:
                    offenders.append(str(path))
        assert not offenders, (
            f"code outside the allowlist reaches availability: {offenders}. "
            "It is display context plus a ranker feature; the planner and "
            "value model may not read it."
        )
