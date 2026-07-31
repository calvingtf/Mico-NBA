"""How each 2026-27 arrival actually arrived, sourced rather than inferred.

M5 scored a signing planner against every player who changed teams, which put
trades in the denominator and bounded recall below 1 by construction. Worse, it
put *pre-freeze* trades there: Giannis Antetokounmpo was treated as something
Miami had yet to acquire, so his $58.5M came off their freeze state and handed
them roughly $100M of cap space that did not exist. Miami then won all eight
contested players, which read as a defect in the contention rule and was
substantially a defect in the freeze state.

The ingest cannot settle this. ``signed_on`` and ``acquired_via_trade_on`` are
empty in every row, and the transaction log stops on 2026-07-09 — before most
of these moves. So the mechanism is sourced from reporting, one arrival at a
time, with a URL and a retrieval date, and anything not sourced says so.

The dates matter as much as the mechanisms. Three of the largest moves happened
*before* the freeze and are therefore inputs, not predictions:

    2026-06-22  Giannis Antetokounmpo and Bobby Portis, Milwaukee -> Miami
    2026-06-25  LaMelo Ball and Josh Green, Charlotte -> Minnesota
    2026-07-06  Jaylen Brown, Boston -> Philadelphia (reported 07-01)

Jaylen Brown sits exactly on the boundary: reported July 1 and official July 6,
the freeze date. The ledger's rule is ``date <= freeze`` is PRE, so he is an
input — which is the reading that also matches the world, since Philadelphia
was negotiating with LeBron James knowing Brown was theirs.

That last one decides whether the simulation can reproduce the defining move of
the scenario. With Brown's $57,078,728 on the books Philadelphia has no room,
and the minimum exception is the only way to add LeBron James — which is what
actually happened, at $3,876,529.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Mechanism labels. A signing planner can only ever produce SIGNING.
SIGNING = "signing"
TRADE = "trade"
DRAFT = "draft"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Arrival:
    player_id: str
    team: str
    mechanism: str
    when: date | None
    source: str
    url: str
    retrieved: date
    note: str = ""

    @property
    def pre_freeze(self) -> bool:
        return self.when is not None and self.when <= date(2026, 7, 6)

    @property
    def producible_by_a_signing_planner(self) -> bool:
        """Whether a planner that only signs free agents could have made this."""
        return self.mechanism == SIGNING and not self.pre_freeze


R = date(2026, 7, 31)

ARRIVALS: tuple[Arrival, ...] = (
    # --- pre-freeze trades: inputs, not predictions -----------------------
    Arrival("antetgi01", "MIA", TRADE, date(2026, 6, 22), "ESPN",
            "https://www.espn.com/nba/story/_/id/49144931/giannis-traded-heat-faq-blockbuster-move-means-bucks-celtics-draft-playoff-race",
            R, "Milwaukee -> Miami with Portis for Herro, Ware, Jaquez, "
               "Jakucionis and picks; completed the night of June 22"),
    Arrival("portibo01", "MIA", TRADE, date(2026, 6, 22), "ESPN",
            "https://www.espn.com/nba/story/_/id/49144931/giannis-traded-heat-faq-blockbuster-move-means-bucks-celtics-draft-playoff-race",
            R, "same trade as Antetokounmpo"),
    Arrival("ballla01", "MIN", TRADE, date(2026, 6, 25), "NBC Sports",
            "https://www.nbcsports.com/fantasy/basketball/player-news/2026-06-25/shams-lamelo-ball-traded-to-timberwolves",
            R, "Charlotte -> Minnesota with Josh Green for Naz Reid and picks"),
    Arrival("greenjo02", "MIN", TRADE, date(2026, 6, 25), "NBC Sports",
            "https://www.nbcsports.com/fantasy/basketball/player-news/2026-06-25/shams-lamelo-ball-traded-to-timberwolves",
            R, "same trade as LaMelo Ball"),
    Arrival("brownja02", "PHI", TRADE, date(2026, 7, 6), "NBA.com / Boston Globe",
            "https://www.nba.com/news/reports-sixers-to-acquire-jaylen-brown-from-celtics",
            R, "Boston -> Philadelphia for Paul George and picks; reported "
               "July 1, official July 6 — on the freeze boundary, and PRE "
               "under the ledger's date <= freeze rule"),

    # --- post-freeze signings: the only ones a signing planner can make ---
    Arrival("bassech01", "GSW", SIGNING, date(2026, 7, 9),
            "Basketball-Reference transaction log",
            "https://www.basketball-reference.com/leagues/NBA_2026_transactions.html",
            date(2026, 7, 30), "in our own ingest"),
    Arrival("jamesle01", "PHI", SIGNING, date(2026, 7, 24), "ESPN",
            "https://www.espn.com/nba/story/_/id/49440164/lebron-chooses-76ers-sign-2-year-8-million-contract",
            R, "2 years, ~$8M, at the veteran minimum"),

    # --- draft picks: a different mechanism again -------------------------
    Arrival("lendeya01", "GSW", DRAFT, date(2026, 6, 25), "NBCS Bay Area",
            "https://www.nbcsportsbayarea.com/nba/golden-state-warriors/remaining-free-agency-moves-draymond-green/1952777/",
            R, "No. 11 overall pick"),

    # --- unsourced ---------------------------------------------------------
    # Modest salaries, changed teams, and no reporting found. Left UNKNOWN
    # rather than guessed: labelling one of these a signing to improve recall
    # would be choosing the denominator to suit the number.
    Arrival("hardati02", "MIA", UNKNOWN, None, "", "", R, "not sourced"),
    Arrival("wadede01", "PHI", UNKNOWN, None, "", "", R, "not sourced"),
    Arrival("simonan01", "PHI", UNKNOWN, None, "", "", R, "not sourced"),
    Arrival("hukpoar01", "PHI", UNKNOWN, None, "", "", R, "not sourced"),
    Arrival("lylestr01", "MIN", UNKNOWN, None, "", "", R, "no 2025-26 contract"),
    Arrival("conwery01", "MIA", UNKNOWN, None, "", "", R, "no 2025-26 contract"),
    Arrival("evansis01", "MIN", UNKNOWN, None, "", "", R, "no 2025-26 contract"),
    Arrival("thomame01", "CLE", UNKNOWN, None, "", "", R, "no 2025-26 contract"),
    Arrival("philola01", "PHI", UNKNOWN, None, "", "", R, "no 2025-26 contract"),
)

BY_ID = {a.player_id: a for a in ARRIVALS}


def mechanism(player_id: str) -> str:
    arrival = BY_ID.get(player_id)
    return arrival.mechanism if arrival else UNKNOWN


def pre_freeze_ids() -> set[str]:
    """Arrivals that had already happened at the freeze. These are inputs."""
    return {a.player_id for a in ARRIVALS if a.pre_freeze}


def signing_targets(team: str) -> set[str]:
    """Arrivals for this team a signing planner could in principle produce."""
    return {
        a.player_id for a in ARRIVALS
        if a.team == team and a.producible_by_a_signing_planner
    }


def summary() -> str:
    counts: dict[str, int] = {}
    for arrival in ARRIVALS:
        key = f"{arrival.mechanism}{' (pre-freeze)' if arrival.pre_freeze else ''}"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
