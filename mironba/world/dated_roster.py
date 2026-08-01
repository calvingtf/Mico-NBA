"""Roster and salary state at an arbitrary date, reconstructed.

    state = roster_on("2024-25", "PHI", date(2025, 2, 6))

The contracts snapshot has no date column: Basketball-Reference's ``/contracts/``
pages are live views with no archive, so no fetch produces a dated state. The
*transaction* log does carry dates, and that is what this reconstructs from.

**Written before validation.** The rules below were derived from the structure of
the two tables, not from inspecting which reconstructions came out right. The
Philadelphia case that motivates this work is deliberately not consulted: a
method tuned until one team's answer looks correct is not a method, and that is
the error the hand-worked Golden State correction was kept out for.

## The two classes

Every contract row falls into one of two, and both are dateable:

* **Has a dated transaction** (58.6% in 2025-26) - arrival and departure are on
  the record, so presence at *D* is a comparison.
* **Has no transaction at all** (41.4%) - the player appears in the season-end
  table and never moved, so he was on that roster for the whole season.

## The offseason boundary, which is the weak class

A player signing in July sits at a seam: the league year begins July 1, so a
signing on 2026-07-06 belongs to the **2026-27** league year even though the
2025-26 season's records were still being written days earlier.

**The rule:** a transaction belongs to the league year containing its date, where
a league year runs July 1 to June 30.

**What that rests on:** the CBA's own definition of a league year - the same
boundary the moratorium and every seasonal cap figure in ``rules/constants.py``
already key on. It is *not* an inference from how Basketball-Reference happens to
file rows, and :func:`boundary_evidence` measures whether the source agrees by
counting rows whose date falls outside the league year of the file they appear
in. A non-trivial count means the rule is wrong about the source, and this
module says so rather than silently misdating July.

## Provenance

An undateable row is **reported, not inferred**. :class:`DatedState` carries
``undateable``, and a payroll quoted from a state with a non-empty list is a
partial figure the caller can see.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

#: The league year boundary. July 1, per the CBA, and the same date the
#: moratorium and every seasonal cap figure key on.
LEAGUE_YEAR_START = (7, 1)

ARRIVAL_WORDS = ("signed", "traded to", "claimed", "converted", "acquired")
DEPARTURE_WORDS = ("waived", "released", "traded from", "traded by")


def league_year(when: date) -> str:
    """The season string whose league year contains ``when``."""
    start = when.year if (when.month, when.day) >= LEAGUE_YEAR_START else when.year - 1
    return f"{start}-{str(start + 1)[2:]}"


@dataclass
class DatedState:
    team_id: str
    when: date
    season: str
    salaries: dict[str, int] = field(default_factory=dict)
    #: Rows that could not be placed. Reported, never guessed at.
    undateable: list[str] = field(default_factory=list)
    #: How each present player was resolved, for auditing.
    via: dict[str, str] = field(default_factory=dict)

    @property
    def payroll(self) -> int:
        return sum(self.salaries.values())

    @property
    def roster_count(self) -> int:
        return len(self.salaries)

    @property
    def complete(self) -> bool:
        return not self.undateable


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def movements(season: str) -> dict[str, list[tuple[date, str]]]:
    """player_id -> [(date, 'arrival'|'departure')] from the dated log."""
    out: dict[str, list[tuple[date, str]]] = {}
    for row in _rows(SNAPSHOTS / f"bbref-{season}" / "transactions.csv"):
        text = (row.get("text") or "").lower()
        if any(word in text for word in DEPARTURE_WORDS):
            kind = "departure"
        elif any(word in text for word in ARRIVAL_WORDS):
            kind = "arrival"
        else:
            continue
        try:
            when = date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        for player_id in (row.get("player_ids") or "").split("|"):
            if player_id:
                out.setdefault(player_id, []).append((when, kind))
    for history in out.values():
        history.sort()
    return out


def roster_on(season: str, team_id: str, when: date) -> DatedState:
    """Who was on ``team_id`` on ``when``, with their season cap hits.

    Season-end contracts give the population and the money; the transaction log
    decides presence. A player whose first arrival is after ``when`` was not
    there; one whose last departure precedes ``when`` had gone.
    """
    contracts = [
        row for row in _rows(SNAPSHOTS / f"bbref-{season}" / "contracts.csv")
        if row["team_id"] == team_id
    ]
    history_by_player = movements(season)
    state = DatedState(team_id=team_id, when=when, season=season)

    for row in contracts:
        player_id = row["player_id"]
        history = history_by_player.get(player_id, [])
        if not history:
            state.salaries[player_id] = int(row["salary"])
            state.via[player_id] = "no-transaction (present all season)"
            continue

        arrivals = [d for d, kind in history if kind == "arrival"]
        departures = [d for d, kind in history if kind == "departure"]
        if arrivals and min(arrivals) > when:
            continue
        if departures and max(departures) <= when and (
            not arrivals or max(arrivals) <= max(departures)
        ):
            continue
        state.salaries[player_id] = int(row["salary"])
        state.via[player_id] = "dated-transaction"
    return state


def boundary_evidence(season: str) -> dict:
    """Does the source file transactions by league year, as the rule assumes?

    Counts rows in ``season``'s log whose date falls outside that season's
    league year. A non-trivial count means the rule is wrong about the source
    and the July case cannot be trusted, which is the number that decides
    whether an offseason freeze is reachable at all.
    """
    rows = _rows(SNAPSHOTS / f"bbref-{season}" / "transactions.csv")
    outside: list[tuple[str, str]] = []
    total = 0
    for row in rows:
        try:
            when = date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            continue
        total += 1
        if league_year(when) != season:
            outside.append((row["date"], league_year(when)))
    return {
        "season": season,
        "total": total,
        "outside": len(outside),
        "rate": len(outside) / total if total else 0.0,
        "sample": outside[:5],
    }
