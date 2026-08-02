"""Does a contract extend past June 30, or does it only look like it does?

    verdict = extends_into(player_id, team_id, season, freeze_date)

The dated roster reconstruction (``dated_roster.py``) decides *presence* from
the transaction log. It cannot decide *expiry*: a player who finished season S
on team X occupies a season-table row whether or not his contract ran past
June 30, so a July reconstruction counts roster slots that are in truth empty.
That is the residual the July validation left named: season-end rosters "full"
of deals that had already ended.

**Written blind, before validation.** The rules below were fixed without
running them against Cleveland, Golden State or Miami — the three teams whose
exclusion motivates this work — and without inspecting any team's 2026-27 rows.
The inputs consulted while writing were the two tables' schemas. Same
discipline as ``dated_roster.py``, for the same reason: a method tuned until
the interesting teams come out right is not a method.

## The direction of failure

**Every unresolvable case occupies the slot.** Freeing a slot is the direction
that admits teams into the suitor check, so absence of evidence must never
free one. There is exactly one deliberate freeing rule, and it rests on a
positive signal, not on ambiguity:

* **No row in the league-year-Y contracts source → EXPIRED.** The Y-source is a
  comprehensive scrape (season-end table historically; the
  ``bbref-contracts-2026-27`` structure snapshot for 2026). A player with no
  contract row for Y has no known contract for Y. Declined options are
  correctly absent from the source, so they resolve to EXPIRED without an
  option-specific rule. The validation measures this rule's error rate, and if
  it fails materially the method stops rather than ships.

## The rules

For player ``p`` on team ``X`` at July date ``D`` (league year ``Y``):

1. **Y-source row on X, no new-deal signing row after D** → EXTENDS. The
   contract continues; the slot is occupied.
2. **Y-source row on X, with a signing/arrival row dated after D** → EXPIRED at
   ``D``. His Y presence came from a *new* deal signed later (a July re-signing
   at the moratorium's end); at ``D`` he was uncovered. Resolvable only where a
   transaction log covers early Y — historically Y's own log; for 2026, the
   handful of early-July rows that leak into the closing season's file.
3. **Y-source row on X, signing row dated on-or-before D** → occupies. He
   re-signed before the freeze; the new deal covers ``D``.
4. **Y-source row on another team** → UNRESOLVED → occupies. Without a log a
   free-agency departure (slot free) cannot be told from an August trade of a
   live contract (slot occupied at ``D``).
5. **No Y-source row** → EXPIRED. The one freeing rule, above.

Option years: presence of a Y row is the signal, whatever the ``option`` flag
says — by the time either source is scraped, option decisions for Y are made,
and an exercised option is just a contract. A declined one produces no row.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mironba.world.dated_roster import ARRIVAL_WORDS, league_year

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

EXTENDS = "extends"
EXPIRED = "expired"
UNRESOLVED = "unresolved"          # always occupies

#: Verdicts that occupy a roster slot. UNRESOLVED occupies by design.
OCCUPIES = frozenset({EXTENDS, UNRESOLVED})


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def year_source(year: str) -> dict[str, str]:
    """player_id -> team_id for league year ``year``, from whichever table exists.

    Historically the season snapshot is comprehensive. For a year with no
    season snapshot yet (2026-27), the contract-structure snapshot stands in:
    its rows are per-future-season, so presence of a ``season == year`` row is
    the same signal.
    """
    season_table = _rows(SNAPSHOTS / f"bbref-{year}" / "contracts.csv")
    if season_table:
        return {r["player_id"]: r["team_id"] for r in season_table}
    structure = _rows(SNAPSHOTS / f"bbref-contracts-{year}" / "contract_years.csv")
    return {
        r["player_id"]: r["team_id"]
        for r in structure if r["season"] == year
    }


def _july_signings(year: str, closing_season: str) -> dict[str, date]:
    """player_id -> earliest signing date in early league-year-Y coverage.

    Historically Y's own log covers July onward. For 2026 no Y log exists, but
    a few early-July rows leak into the closing season's file (the
    july-in-closing class from the boundary audit) — they are read here, which
    is the only early-Y visibility the 2026 case has.
    """
    out: dict[str, date] = {}
    for season in (year, closing_season):
        for row in _rows(SNAPSHOTS / f"bbref-{season}" / "transactions.csv"):
            text = (row.get("text") or "").lower()
            if not any(word in text for word in ARRIVAL_WORDS):
                continue
            try:
                when = date.fromisoformat(row["date"])
            except (KeyError, ValueError):
                continue
            if league_year(when) != year:
                continue
            for pid in (row.get("player_ids") or "").split("|"):
                if pid and (pid not in out or when < out[pid]):
                    out[pid] = when
    return out


@dataclass(frozen=True, slots=True)
class ExpiryCall:
    player_id: str
    verdict: str
    reason: str

    @property
    def occupies_slot(self) -> bool:
        return self.verdict in OCCUPIES


def extends_into(
    player_id: str,
    team_id: str,
    season: str,
    freeze: date,
    *,
    _source: dict[str, str] | None = None,
    _signings: dict[str, date] | None = None,
) -> ExpiryCall:
    """Whether ``player_id``'s contract covers ``freeze``. Rules in the docstring."""
    year = league_year(freeze)
    if year == season:
        # An in-season date: the season table itself covers it; expiry is not
        # in question and the dated roster already decides presence.
        return ExpiryCall(player_id, EXTENDS, "in-season date; season table governs")
    source = _source if _source is not None else year_source(year)
    signings = _signings if _signings is not None else _july_signings(year, season)

    if not source:
        return ExpiryCall(
            player_id, UNRESOLVED,
            f"no contracts source for {year}; occupying by default",
        )
    team_next = source.get(player_id)
    if team_next is None:
        return ExpiryCall(
            player_id, EXPIRED,
            f"no {year} contract row anywhere; the one freeing rule",
        )
    signed = signings.get(player_id)
    if team_next == team_id:
        if signed and signed > freeze:
            return ExpiryCall(
                player_id, EXPIRED,
                f"re-signed {signed}, after the freeze; uncovered at {freeze}",
            )
        if signed and signed <= freeze:
            return ExpiryCall(
                player_id, EXTENDS, f"re-signed {signed}, before the freeze"
            )
        return ExpiryCall(player_id, EXTENDS, f"continuing deal on {team_id}")
    return ExpiryCall(
        player_id, UNRESOLVED,
        f"on {team_next} in {year}; departure vs later trade not resolvable",
    )
