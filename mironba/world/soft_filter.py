"""Which teams pursue a star veteran in July — measured from precedent, not asked.

**Written blind.** This module was committed before being executed, and it was
written without reference to the reported suitor list for the LeBron scenario —
a list the author knows, which is exactly why the threshold cannot be a number:
any hand-chosen cutoff could be tuned to that list without anyone being able to
tell. The band is therefore *computed from historical precedent at runtime*,
the same way the deadline disposition bands were measured over 90 team-seasons
rather than chosen.

## The principle

A team is a plausible suitor for a star veteran free agent **if teams with its
previous-season record have actually signed star veterans.** The admission band
is the coverage of nine offseasons of precedent:

    admit team T  iff  prev_win_pct(T) >= min(precedent win_pcts)

At or above the worst record that has really done it is plausible; below every
record that has ever done it is not. No percentile, no margin, no knob.

## Star-priced veteran, defined by rule

* **years of service >= 10** at signing, and
* **prior-season salary >= 20% of that season's salary cap** — the scale of
  money only stars are paid (a rookie-scale maximum *starts* at 25% of cap).

Both inputs exist for all ten ingested seasons; the cap is sourced per season,
so the star line moves with the league rather than going stale.

## The direction of failure is ADMIT

Excluding a real suitor is the worse error — the Philadelphia case established
that, at cost. So:

* a team whose previous-season record is unknown → **admitted**;
* a candidate precedent whose service years cannot be resolved → excluded from
  the *measurement* (and counted), never used to exclude a team;
* fewer than ``MIN_PRECEDENTS`` precedents found → the filter declares itself
  **unusable and admits everyone, loudly** — a band fitted to a handful of
  events would be noise wearing a threshold's clothes.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

#: Below this many historical precedents, the band is noise and the filter
#: refuses to filter.
MIN_PRECEDENTS = 10

#: Star line: prior-season salary as a share of that season's cap.
STAR_CAP_SHARE = 0.20

#: Veteran line: years of service at signing.
VETERAN_YOS = 10

#: The offseasons measured. Each entry is (league year whose July is measured,
#: the season before it — the record and salary that precede the signing).
OFFSEASONS = (
    ("2017-18", "2016-17"), ("2018-19", "2017-18"), ("2019-20", "2018-19"),
    ("2020-21", "2019-20"), ("2021-22", "2020-21"), ("2022-23", "2021-22"),
    ("2023-24", "2022-23"), ("2024-25", "2023-24"), ("2025-26", "2024-25"),
)


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def season_win_pct(season: str) -> dict[str, float]:
    """Full-season win percentage per team, from the dated game log."""
    from mironba.models.disposition import standings_on

    end_year = int(season[:4]) + 1
    standings = standings_on(season, date(end_year, 12, 31))
    return {team: s.win_pct for team, s in standings.items()}


@dataclass
class Precedent:
    league_year: str
    player_id: str
    team_id: str
    prev_win_pct: float


@dataclass
class PrecedentSet:
    precedents: list[Precedent] = field(default_factory=list)
    #: Candidate signings dropped because service years could not be resolved.
    #: Counted, because a lossy join must say so (entry 27).
    unresolved_service: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return len(self.precedents) >= MIN_PRECEDENTS

    @property
    def floor(self) -> float:
        return min(p.prev_win_pct for p in self.precedents)


def star_veteran_precedents() -> PrecedentSet:
    """Every July–September star-veteran signing across the measured offseasons.

    Service years come from the nba_api careers join — an *input* about a
    player's history, not an outcome; the layering rule guards outcomes.
    """
    from mironba.eval.real_trades import _service_years
    from mironba.rules.constants import environment_for

    out = PrecedentSet()
    for league_year, prior in OFFSEASONS:
        salaries = {
            r["player_id"]: int(r["salary"])
            for r in _rows(SNAPSHOTS / f"bbref-{prior}" / "contracts.csv")
        }
        if not salaries:
            out.notes.append(f"{prior}: no contracts table; offseason skipped")
            continue
        star_line = environment_for(prior).salary_cap * STAR_CAP_SHARE
        service = _service_years(prior)
        records = season_win_pct(prior)
        if not records:
            out.notes.append(f"{prior}: no game log; offseason skipped")
            continue
        start_year = int(league_year[:4])
        window = (date(start_year, 7, 1), date(start_year, 9, 30))
        for row in _rows(SNAPSHOTS / f"bbref-{league_year}" / "transactions.csv"):
            text = (row.get("text") or "").lower()
            if "signed" not in text or "traded" in text:
                continue
            try:
                when = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if not (window[0] <= when <= window[1]):
                continue
            teams = [t for t in (row.get("team_ids") or "").split("|") if t]
            if len(teams) != 1 or teams[0] not in records:
                continue
            for pid in (row.get("player_ids") or "").split("|"):
                if not pid or salaries.get(pid, 0) < star_line:
                    continue
                years = service.get(pid)
                if years is None:
                    out.unresolved_service += 1
                    continue
                if years >= VETERAN_YOS:
                    out.precedents.append(
                        Precedent(league_year, pid, teams[0], records[teams[0]])
                    )
    return out


@dataclass(frozen=True, slots=True)
class SoftCall:
    team_id: str
    admitted: bool
    reason: str


def soft_admits(previous_season: str) -> dict[str, SoftCall]:
    """Admit every team whose previous-season record clears the precedent floor.

    Ambiguity admits: an unknown record admits, an unusable precedent set
    admits everyone. The only exclusion is a positive one — a record below
    every record that has ever preceded a star-veteran signing.
    """
    precedents = star_veteran_precedents()
    records = season_win_pct(previous_season)
    out: dict[str, SoftCall] = {}
    if not precedents.usable:
        for team in records or ():
            out[team] = SoftCall(
                team, True,
                f"filter unusable: only {len(precedents.precedents)} precedents "
                f"(< {MIN_PRECEDENTS}); admitting everyone rather than filtering "
                "on noise",
            )
        return out
    floor = precedents.floor
    for team, win_pct in records.items():
        if win_pct >= floor:
            out[team] = SoftCall(
                team, True,
                f"{win_pct:.3f} >= precedent floor {floor:.3f} "
                f"(n={len(precedents.precedents)} signings, 9 offseasons)",
            )
        else:
            out[team] = SoftCall(
                team, False,
                f"{win_pct:.3f} below every record that has preceded a "
                f"star-veteran signing (floor {floor:.3f})",
            )
    return out
