"""League-graph data, read from committed artifacts. Computes no results.

Every node and edge here comes out of a recorded run's manifest - the
per-team reaction (payroll before/after, signings, lost contests) and the
cascade's generated trades - joined to the contract snapshot for roster
counts. Nothing is simulated, scored or inferred: the import fence keeps
``mironba.sim``, ``mironba.models`` and ``mironba.eval`` out of this
package, so a graph that looked like a result would have to be one.

**What the colours honestly say.** Node colour is the team's PAYROLL BAND
against the published CBA thresholds for the season - committed contract
data measured against published constants, which is presentation, not
modelling. The buyer/seller *disposition* that drove the cascade lives in
``models/disposition.py`` and is fenced out of the UI, so it is never
recomputed here; what the artifact does prove is structural, and is
labelled as such: the cascade's counterparty gate admits **only SELLER
teams as counterparties**, so a team recorded as a counterparty was
seller-classified at run time, and a team recorded as an acquirer passed
the not-a-seller gate. That is read off the record, not re-derived.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
SNAPSHOTS = ROOT / "mironba" / "data" / "snapshots"

#: Fixed geographic-ish layout: each team's real city projected onto a
#: 0-100 box (x = west->east, y = north->south), with co-located pairs
#: nudged apart. A force layout re-arranges on every load and tells the
#: reader nothing true; geography is stable and legible, so the graph is
#: comparable between runs. Percentages, so the SVG scales freely.
TEAM_XY = {
    "POR": (4.5, 18.0), "SEA": (3.0, 12.0),
    "GSW": (5.0, 49.0), "SAC": (6.6, 45.5),
    "LAL": (11.0, 63.5), "LAC": (7.6, 66.5),
    "PHX": (23.5, 66.5), "UTA": (23.8, 37.0),
    "DEN": (36.0, 41.5), "MIN": (57.5, 20.5),
    "OKC": (50.0, 58.0), "DAL": (52.5, 69.0),
    "SAS": (48.0, 82.5), "HOU": (54.0, 81.0),
    "NOP": (63.5, 80.0), "MEM": (63.5, 59.5),
    "MIL": (67.5, 28.0), "CHI": (68.5, 32.8),
    "IND": (70.8, 41.0), "DET": (76.5, 30.5),
    "CLE": (78.8, 34.0), "ATL": (73.8, 65.0),
    "MIA": (81.5, 96.5), "ORL": (79.0, 86.0),
    "CHA": (80.5, 59.0), "WAS": (87.0, 44.5),
    "PHI": (90.5, 40.0), "NYK": (92.5, 34.5),
    "BKN": (95.5, 38.5), "BOS": (97.0, 30.5),
    "TOR": (82.5, 25.5),
}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _payroll_bands(season: str) -> dict:
    """The published CBA thresholds. Constants, not a model."""
    from mironba.rules.constants import environment_for

    env = environment_for(season)
    return {"cap": env.salary_cap, "tax": env.tax_line,
            "apron1": env.first_apron, "apron2": env.second_apron}


def _roster_counts(season: str) -> dict:
    path = SNAPSHOTS / f"bbref-contracts-{season}" / "contract_years.csv"
    counts: dict[str, int] = {}
    if not path.is_file():
        return counts
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["season"] == season:
                counts[row["team_id"]] = counts.get(row["team_id"], 0) + 1
    return counts


def band_of(payroll: int, bands: dict) -> str:
    if payroll >= bands["apron2"]:
        return "apron2"
    if payroll >= bands["apron1"]:
        return "apron1"
    if payroll >= bands["tax"]:
        return "tax"
    if payroll >= bands["cap"]:
        return "cap"
    return "room"


BAND_LABEL = {
    "apron2": "over the second apron",
    "apron1": "over the first apron",
    "tax": "over the tax line",
    "cap": "over the cap",
    "room": "under the cap",
}


def stipulated_runs() -> list:
    """Runs whose manifest carries a reaction + cascade (the graph's food)."""
    out = []
    for run_dir in sorted((d for d in RUNS.iterdir() if d.is_dir()),
                          key=lambda d: d.stat().st_mtime, reverse=True):
        path = run_dir / "manifest.json"
        if not path.is_file():
            continue
        try:
            manifest = _read_json(path)
        except Exception:  # noqa: BLE001 - a half-written manifest is not a graph
            continue
        if "reaction" in manifest and "cascade" in manifest:
            out.append((run_dir.name, manifest))
    return out


def league_graph(run_id: str | None = None) -> dict:
    """Nodes and edges for one recorded run. Reading only."""
    runs = stipulated_runs()
    if not runs:
        return {}
    if run_id:
        chosen = next((r for r in runs if r[0] == run_id), None)
        if chosen is None:
            return {}
    else:
        chosen = runs[0]
    run_id, manifest = chosen

    season = manifest.get("data_snapshot") or "2026-27"
    bands = _payroll_bands(season)
    rosters = _roster_counts(season)
    reaction = manifest.get("reaction", {})
    cascade = manifest.get("cascade", {})
    trades = cascade.get("seeded_trades", [])
    attributable = {json.dumps(t, sort_keys=True)
                    for t in cascade.get("attributable_to_seed", [])}

    from mironba.report.timeline import name_of

    # who signed whom, so a lost contest can name the team that won it
    winner_of: dict[str, str] = {}
    for team, row in reaction.items():
        for pid in row.get("signed", []):
            winner_of[pid] = team

    # structural roles the record proves (see the module docstring)
    counterparties = {t["counterparty"] for t in trades}
    acquirers = {t["acquirer"] for t in trades}

    seed_players = {p["player_id"]: (p["from"], p["to"])
                    for p in manifest.get("trade", {}).get("players", [])}
    seed_teams = {t for pair in seed_players.values() for t in pair}

    nodes = []
    for team, xy in sorted(TEAM_XY.items()):
        row = reaction.get(team)
        if row is None and team not in seed_teams:
            continue
        payroll = (row or {}).get("committed_end") or 0
        start = (row or {}).get("committed_start") or 0
        nodes.append({
            "team": team, "x": xy[0], "y": xy[1],
            "payroll": payroll, "payroll_start": start,
            "band": band_of(payroll, bands),
            "band_label": BAND_LABEL[band_of(payroll, bands)],
            "roster": rosters.get(team, 0),
            "signed": [name_of(p) for p in (row or {}).get("signed", [])],
            "lost": [name_of(p) for p in (row or {}).get("lost_contests", [])],
            "role": ("seller-gated counterparty" if team in counterparties
                     else "passed the not-a-seller gate" if team in acquirers
                     else ""),
            "seed": team in seed_teams,
        })

    edges = []
    for i, trade in enumerate(trades):
        key = json.dumps(trade, sort_keys=True)
        edges.append({
            "kind": "trade", "order": i,
            "source": trade["counterparty"], "target": trade["acquirer"],
            "label": (f"{trade['acquirer']} acquires "
                      + ", ".join(name_of(p) for p in trade["received"])
                      + f" from {trade['counterparty']} for "
                      + ", ".join(name_of(p) for p in trade["sent"])),
            "attributable": key in attributable,
            "trigger": trade.get("trigger", ""),
        })
    for team, row in sorted(reaction.items()):
        for pid in row.get("lost_contests", []):
            winner = winner_of.get(pid)
            if winner and winner != team:
                edges.append({
                    "kind": "contest", "order": len(edges),
                    "source": team, "target": winner,
                    "label": f"{team} lost {name_of(pid)} to {winner}",
                    "attributable": False, "trigger": "",
                })

    seed_edges = [{
        "kind": "seed", "order": -1, "source": frm, "target": to,
        "label": f"STIPULATED: {name_of(pid)} {frm} → {to}",
        "attributable": True, "trigger": "the seed event",
    } for pid, (frm, to) in seed_players.items()]

    return {
        "run_id": run_id,
        "scenario": manifest.get("scenario", ""),
        "unfalsifiable": manifest.get("unfalsifiable", False),
        "season": season, "bands": bands,
        "nodes": nodes,
        "edges": seed_edges + edges,
        "seed_label": manifest.get("trade", {}).get("label", ""),
        "counts": {
            "trades": len(trades),
            "attributable": len(cascade.get("attributable_to_seed", [])),
            "unseeded": len(cascade.get("unseeded_trades", [])),
            "gate_kills": cascade.get("killed_by_counterparty_gate", 0),
        },
        "runs": [r[0] for r in runs],
    }


def hero_frames(limit: int = 8) -> dict:
    """The landing hero: the seed event, then the cascade in order.

    Same data as the graph, trimmed to what reads at a glance.
    """
    graph = league_graph()
    if not graph:
        return {}
    edges = [e for e in graph["edges"] if e["kind"] in ("seed", "trade")]
    return {
        "run_id": graph["run_id"], "seed_label": graph["seed_label"],
        "unfalsifiable": graph["unfalsifiable"],
        "nodes": {n["team"]: n for n in graph["nodes"]},
        "edges": edges[:limit],
        "counts": graph["counts"],
    }
