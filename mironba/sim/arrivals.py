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
    freeze: date = date(2026, 7, 6)

    @property
    def pre_freeze(self) -> bool:
        return self.when is not None and self.when <= self.freeze

    @property
    def producible_by_a_signing_planner(self) -> bool:
        """Whether a planner that only signs free agents could have made this."""
        return self.mechanism == SIGNING and not self.pre_freeze


R = date(2026, 7, 31)

def load_arrivals(scenario) -> tuple:
    """The dated arrival table for one scenario, from its evidence store.

    This was a hand-written module constant for the LeBron case - sourced,
    dated data living in code. It is data: evidence/<id>/arrivals.csv, same
    provenance columns, loaded per scenario. The freeze that splits pre from
    post comes from the scenario, never from a constant.
    """
    import csv as _csv

    path = scenario.evidence_dir / 'arrivals.csv'
    if not path.is_file():
        return ()
    out = []
    with path.open(encoding='utf-8', newline='') as handle:
        for r in _csv.DictReader(handle):
            out.append(Arrival(
                player_id=r['player_id'], team=r['team'],
                mechanism=r['mechanism'],
                when=date.fromisoformat(r['when']) if r['when'] else None,
                source=r['source'], url=r['url'],
                retrieved=date.fromisoformat(r['retrieved']),
                note=r.get('note', ''),
                freeze=scenario.freeze,
            ))
    return tuple(out)

def mechanism(player_id: str, arrivals) -> str:
    for arrival in arrivals:
        if arrival.player_id == player_id:
            return arrival.mechanism
    return UNKNOWN


def pre_freeze_ids(arrivals) -> set[str]:
    """Arrivals that had already happened at the freeze. These are inputs."""
    return {a.player_id for a in arrivals if a.pre_freeze}


def signing_targets(team: str, arrivals) -> set[str]:
    """Arrivals for this team a signing planner could in principle produce."""
    return {
        a.player_id for a in arrivals
        if a.team == team and a.producible_by_a_signing_planner
    }


def summary(arrivals) -> str:
    counts: dict[str, int] = {}
    for arrival in arrivals:
        key = f"{arrival.mechanism}{' (pre-freeze)' if arrival.pre_freeze else ''}"
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
