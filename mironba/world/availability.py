"""As-of availability from player game logs. Display context, never an input.

A player with zero appearances in his team's last N games before a date was
unavailable on that date - injured, benched, away, it does not matter which;
the appearance record is the observable. Derived **as-of**: every question
names its own date, nothing in this module can read a clock, and a test greps
for exactly that.

Two boundaries, both enforced by tests rather than intention:

* **Not a sim input.** No module under ``mironba/sim``, ``mironba/agents``,
  ``mironba/models`` or ``mironba/rules`` may import this one. Availability
  is rendered beside the teams on the scenario surface so a reader has the
  context; the planner never sees it, because nothing about its effect on a
  GM's behaviour has been measured.
* **Uncertain rows say so.** The roster comes from the contract table
  (bbref ids) and the logs from the stats table (names); the join is by
  normalised name and reported with a hit rate like every other join. A
  player who also appeared for another team inside the window is flagged
  TRADED-IN; one with no log row all season is NO-LOG-ROW (two-way and
  never-activated players land here - the snapshot cannot tell them apart,
  and this module does not guess).
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mironba.data.joins import Join

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
TRADED_IN = "TRADED-IN"
NO_LOG_ROW = "NO-LOG-ROW"

CONTEXT_MARKER = (
    "Availability is DISPLAY CONTEXT derived from appearance records as of "
    "the stated date. It is not a value-model input and not a planner input; "
    "a test asserts no sim path can read it."
)


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", text.lower())


@dataclass(frozen=True)
class Appearance:
    player_name: str
    team: str
    game_date: date


def load_player_logs(season: str, root: Path = SNAPSHOTS) -> list[Appearance]:
    path = root / "nba-stats" / "player_game_logs.csv"
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["season"] != season:
                continue
            out.append(Appearance(
                player_name=row["PLAYER_NAME"],
                team=row["TEAM_ABBREVIATION"],
                game_date=date.fromisoformat(row["GAME_DATE"]),
            ))
    return out


def team_last_games(team: str, as_of: date, logs: list[Appearance],
                    n: int = 10) -> list[date]:
    """The dates of the team's last n games STRICTLY before as_of."""
    dates = sorted({a.game_date for a in logs
                    if a.team == team and a.game_date < as_of})
    return dates[-n:]


@dataclass(frozen=True)
class PlayerAvailability:
    player_id: str
    name: str
    appearances: int
    window_games: int
    status: str
    note: str = ""

    def line(self) -> str:
        return (f"    {self.name:<24} {self.appearances:>2}/{self.window_games:<2} "
                f"of last games  {self.status}"
                + (f"  ({self.note})" if self.note else ""))


def availability(team: str, as_of: date, roster: dict[str, str],
                 logs: list[Appearance], n: int = 10
                 ) -> tuple[list[PlayerAvailability], Join]:
    """As-of availability for a roster. roster maps player_id -> display name.

    Returns the rows and the name Join so callers can report the hit rate the
    way every other join reports one.
    """
    window = set(team_last_games(team, as_of, logs, n))
    by_player: dict[str, set[date]] = {}
    teams_by_player: dict[str, set[str]] = {}
    for a in logs:
        if a.game_date >= as_of:
            continue
        key = _norm(a.player_name)
        teams_by_player.setdefault(key, set()).add(a.team)
        if a.team == team and a.game_date in window:
            by_player.setdefault(key, set()).add(a.game_date)

    logged_names = {_norm(a.player_name) for a in logs if a.game_date < as_of}
    join = Join(name=f"{team} roster -> player logs", table={k: k for k in logged_names},
                max_miss_rate=0.5)

    rows = []
    for pid, display in sorted(roster.items(), key=lambda kv: kv[1]):
        key = join.get(_norm(display))
        if key is None:
            rows.append(PlayerAvailability(pid, display, 0, len(window), NO_LOG_ROW,
                                           "no appearance row this season; "
                                           "two-way and never-activated players "
                                           "are indistinguishable here"))
            continue
        played = len(by_player.get(key, ()))
        status = AVAILABLE if played > 0 else UNAVAILABLE
        note = ""
        other = teams_by_player.get(key, set()) - {team}
        if played == 0 and other:
            status = TRADED_IN
            note = (f"appeared for {'/'.join(sorted(other))} inside the season; "
                    "the window predates his arrival")
        rows.append(PlayerAvailability(pid, display, played, len(window), status, note))
    return rows, join


def render_availability(team: str, as_of: date, roster: dict[str, str],
                        logs: list[Appearance], n: int = 10) -> str:
    """Terminal block for the scenario surface. Context only, and says so."""
    rows, join = availability(team, as_of, roster, logs, n)
    if not rows:
        return ""
    lines = [f"  {team} - appearances in the team's last {n} games before {as_of}"]
    lines += [r.line() for r in rows]
    lines.append(f"    [{join.report().strip()}]")
    lines.append(f"  {CONTEXT_MARKER}")
    return chr(10).join(lines)
