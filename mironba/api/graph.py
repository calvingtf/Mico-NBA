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
    return {"cap": env.salary_cap, "tax": env.tax_level,
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
    if not RUNS.is_dir():
        # A fresh clone. Nothing has been run; that is a state, not a fault.
        return out
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
    """The graph for one run, chosen by id or newest-first.

    Kept for the standalone page and the hero. ``run_graph`` is the one that
    matters: it draws from a manifest directly, so a scenario authored a
    minute ago has a graph the moment it finishes rather than only once it
    happens to be the newest recorded demo.
    """
    runs = stipulated_runs()
    if not runs:
        return {}
    if run_id:
        chosen = next((r for r in runs if r[0] == run_id), None)
        if chosen is None:
            return {}
    else:
        chosen = runs[0]
    return run_graph(chosen[1], chosen[0], all_runs=[r[0] for r in runs])


def run_graph(manifest: dict, run_id: str, all_runs=None) -> dict:
    """Nodes and edges for ONE run's manifest. Reading only.

    Draws for any manifest carrying a reaction. A run with no generated
    trades, a cascade that terminated at depth zero, or no contested player
    is not a broken graph - it is a graph with fewer edges, and the thin
    cases are named in ``notes`` so the page can say WHICH is the case
    instead of rendering a bare grid of nodes and letting a reader guess.
    """
    if not manifest.get("reaction"):
        return {}

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

    # A signing run has no trade at all - manifest["trade"] is null, not an
    # empty dict - so the seed edges come from whichever kind it carries.
    seed_players = {p["player_id"]: (p["from"], p["to"])
                    for p in (manifest.get("trade") or {}).get("players", [])}
    seed_signing = manifest.get("signing") or {}
    if seed_signing:
        seed_players[seed_signing["player_id"]] = ("", seed_signing["to"])
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
            # DISPOSITION AS THE RECORD PROVES IT, never recomputed: the
            # cascade's counterparty gate admits only SELLER teams, so a
            # recorded counterparty was seller-classified at run time and a
            # recorded acquirer passed the not-a-seller gate. A team that
            # did not participate is unclassified - the artifact does not
            # say, and the UI does not get to guess.
            "disposition": ("seller" if team in counterparties
                            else "buyer-side" if team in acquirers
                            else "unclassified"),
            "role": ("recorded as a counterparty - the cascade gate admits "
                     "only SELLER teams" if team in counterparties
                     else "recorded as an acquirer - passed the not-a-seller "
                          "gate" if team in acquirers
                     else "did not participate; the record does not classify "
                          "it and the UI does not guess"),
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

    # endpoints resolved here, not in the template: a view that has to
    # search for coordinates is a view doing work
    xy = {n["team"]: (n["x"], n["y"]) for n in nodes}
    drawable = []
    for edge in seed_edges + edges:
        if edge["source"] in xy and edge["target"] in xy:
            (x1, y1), (x2, y2) = xy[edge["source"]], xy[edge["target"]]
            drawable.append(dict(edge, x1=x1, y1=y1, x2=x2, y2=y2))

    by_kind = {"seed": 0, "trade": 0, "contest": 0}
    for edge in drawable:
        by_kind[edge["kind"]] = by_kind.get(edge["kind"], 0) + 1

    # THE THIN CASES, NAMED. Each of these is a real and reportable outcome
    # of a run, not a rendering failure, and each looks identical to a
    # broken graph unless the page says which it is.
    notes = []
    if not trades:
        notes.append(
            "NO GENERATED TRADES. The cascade proposed none that survived "
            f"the gates - {cascade.get('killed_by_counterparty_gate', 0)} "
            "candidate pairs were killed by the counterparty gate and "
            f"{cascade.get('killed_by_solver', 0)} by the solver finding no "
            "legal package. The absence of trade edges below is that "
            "result, not a missing layer.")
    elif not attributable:
        notes.append(
            f"NO TRADE IS ATTRIBUTABLE TO THE SEED. All {len(trades)} "
            "generated trades also happen in the run without it, so no edge "
            "below is highlighted as caused. A cascade that would have "
            "happened anyway is not a cascade.")
    if cascade and cascade.get("depth_reached") == 0:
        notes.append(
            "THE CASCADE TERMINATED AT DEPTH ZERO. Nothing woke a second "
            "round: the seed's direct consequences produced no further "
            "intent that reached the solver.")
    if by_kind["contest"] == 0:
        notes.append(
            "NO CONTESTED PLAYERS CHANGED HANDS. Either no player drew "
            "offers from more than one team, or every contest was won by a "
            "team that lost none - so there are no contested-player edges "
            "to draw.")
    if by_kind["seed"] == 0:
        notes.append(
            "NO SEED EDGE. The manifest records no stipulated trade or "
            "signing with both endpoints on the map - a signing seed has "
            "only a destination, so it colours a node rather than drawing "
            "a line.")
    if not drawable:
        notes.append(
            f"NO EDGES AT ALL. The {len(nodes)} node(s) below are the league "
            "at the freeze, with payrolls from this run's own reaction "
            "record. Nothing connected them, and that is the run's result. "
            "(Counted, not assumed: a hardcoded 'thirty' here was wrong "
            "for any run whose reaction covers fewer teams.)")

    return {
        "run_id": run_id,
        "scenario": manifest.get("scenario", ""),
        "unfalsifiable": manifest.get("unfalsifiable", False),
        "season": season, "bands": bands,
        "nodes": nodes,
        "edges": drawable,
        "by_kind": by_kind,
        "notes": notes,
        "thin": bool(notes),
            "seed_label": (manifest.get("trade")
                           or manifest.get("signing")
                           or {}).get("label", ""),
        "counts": {
            "trades": len(trades),
            "attributable": len(cascade.get("attributable_to_seed", [])),
            "unseeded": len(cascade.get("unseeded_trades", [])),
            "gate_kills": cascade.get("killed_by_counterparty_gate", 0),
        },
        "runs": list(all_runs or []),
    }


def hero_frames(limit: int = 8) -> dict:
    """The landing hero: the seed event, then the cascade in order.

    Same data as the graph, trimmed to what reads at a glance.
    """
    graph = league_graph()
    if not graph:
        return {}
    edges = [e for e in graph["edges"] if e["kind"] in ("seed", "trade")]
    # hero nodes keep their coordinates for the inline SVG
    return {
        "run_id": graph["run_id"], "seed_label": graph["seed_label"],
        "unfalsifiable": graph["unfalsifiable"],
        "nodes": {n["team"]: n for n in graph["nodes"]},
        "edges": edges[:limit],
        "counts": graph["counts"],
    }


# --------------------------------------------------------------------------
# Headline numbers and season series - every one paired with its own null
# --------------------------------------------------------------------------


def headline_numbers() -> list:
    """(label, observed, null, unit, note) read from recorded bench files.

    Every entry carries the null it is measured against, because the
    animation is the point: a bar that travels from its null to its
    observed value SHOWS the gap, and a gap of nothing looks like nothing.
    """
    out = []

    ranker = ROOT / "bench-player-ranker.json"
    if ranker.is_file():
        bench = _read_json(ranker)
        seasons = bench["per_season"]
        observed = 100 * sum(r["p_at_k"] for r in seasons) / len(seasons)
        null = 100 * sum(r["wt_null_p_at_k"] for r in seasons) / len(seasons)
        out.append({
            "label": f"player ranker, precision@{bench.get('k', 25)}",
            "observed": round(observed, 1), "null": round(null, 1),
            "unit": "%",
            "note": "against the WITHIN-TEAM permutation null, which "
                    "preserves each team's trade frequency; 10 deadlines",
        })

    pooled = ROOT / "bench-pooled-10season.json"
    if pooled.is_file():
        bench = _read_json(pooled)
        out.append({
            "label": "deadline precision, 10 seasons",
            "observed": round(bench["precision"], 2),
            "null": 2.58, "unit": "%",
            "note": "proposal-weighted per-season null; statistically clear "
                    "and practically small (+1.06 pts)",
        })

    arms = {}
    for scenario in ARM_SCENARIOS:
        m16 = ROOT / f"bench-m16-{scenario}.json"
        m2 = ROOT / f"bench-m2-{scenario}.json"
        if m16.is_file() and m2.is_file():
            for arm, path in (("blind", m16), ("feasible", m16),
                              ("unlock", m2)):
                row = _read_json(path).get(arm)
                if row:
                    arms.setdefault(arm, []).append(row)
    if arms.get("blind") and arms.get("unlock"):
        def rate(arm, key, weighted=False):
            rows = arms[arm]
            total = sum(r["intents"] for r in rows)
            if weighted:
                return 100 * sum(r[key] * r["intents"] for r in rows) / total
            return 100 * sum(r[key] for r in rows) / total

        out.append({
            "label": "intent satisfiable on the first attempt",
            "observed": round(rate("unlock", "intent_satisfiable_first", True), 1),
            "null": round(rate("blind", "intent_satisfiable_first", True), 1),
            "unit": "%",
            "note": "the unaided arm is the null here: same model, same "
                    "scenarios, only what it was shown changed",
        })
        out.append({
            "label": "named an unreachable target",
            "observed": round(rate("unlock", "intents_naming_an_unreachable_target"), 1),
            "null": round(rate("blind", "intents_naming_an_unreachable_target"), 1),
            "unit": "%", "lower_is_better": True,
            "note": "same three scenarios; the unaided arm is the control",
        })
    return out


ARM_SCENARIOS = ("curry-to-lakers", "mid-flexibility-bulls",
                 "undetermined-byc")


def season_series() -> list:
    """Per-season series with their per-season nulls, for sparklines."""
    out = []
    ranker = ROOT / "bench-player-ranker.json"
    if ranker.is_file():
        bench = _read_json(ranker)
        rows = bench["per_season"]
        out.append({
            "label": f"player ranker p@{bench.get('k', 25)} by held-out season",
            "points": [100 * r["p_at_k"] for r in rows],
            "nulls": [100 * r["wt_null_p_at_k"] for r in rows],
            "labels": [r["season"] for r in rows],
            "null_note": "dashed line: the within-team permutation null, "
                         "recomputed per season",
        })
    csv_path = ROOT / "bench-pooled-10season.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        points, labels = [], []
        for row in rows:
            actual = int(row["actual"]) or 1
            points.append(100 * int(row["matched"]) / actual)
            labels.append(row["season"])
        out.append({
            "label": "deadline recall by season",
            "points": points, "nulls": [], "labels": labels,
            "null_note": "per-season nulls are drawn in the committed figure "
                         "(two seasons fall below theirs)",
        })
    return out


def spark_path(points: list, width: int = 220, height: int = 34) -> str:
    """An SVG polyline for a sparkline. Baseline at zero, never truncated."""
    if not points:
        return ""
    top = max(max(points), 1e-9)
    step = width / max(len(points) - 1, 1)
    return " ".join(
        f"{i * step:.1f},{height - (v / top) * (height - 4):.1f}"
        for i, v in enumerate(points))


def pursuit_view(manifest: dict) -> dict:
    """Who else pursued the stipulated signee, and what they did instead.

    The seeded run cannot answer this and should not be asked to: a
    stipulated player is excluded from the signable pool precisely so he
    cannot sign anywhere else, so no contest for him exists there. The
    UNSEEDED run has one, and every row below is a team that made a legal
    offer under an enumerated route in that run - not a team a model
    thought was interested.
    """
    rows = manifest.get("pursuit") or []
    if not rows:
        return {}
    from mironba.report.timeline import name_of

    signing = manifest.get("signing") or {}
    out = []
    for row in rows:
        out.append({
            "team": row["team"],
            "route": row["route"],
            "amount": row["amount"],
            "won_without_seed": row.get("won_him_without_the_seed", False),
            "did_instead": ", ".join(name_of(p)
                                     for p in row.get("did_instead", [])),
            "missed": ", ".join(name_of(p)
                                for p in row.get("missed_out_on", [])),
            "changed": bool(row.get("did_instead")
                            or row.get("missed_out_on")),
        })
    return {
        "player": signing.get("name", ""),
        "to": signing.get("to", ""),
        "rows": out,
        "n": len(out),
        "n_changed": sum(1 for r in out if r["changed"]),
        "winner": next((r["team"] for r in out if r["won_without_seed"]), ""),
    }


def signing_view(manifest: dict) -> dict:
    """The stipulated signing and every route the destination had."""
    signing = manifest.get("signing") or {}
    if not signing:
        return {}
    return dict(signing, n_routes=len(signing.get("routes", [])))


def obligations_view(manifest: dict) -> dict:
    """Teams the seed FORCED to act, read off the manifest.

    An obligation is not a choice the reaction made - it is a rules finding
    the reaction had to answer. Leading with these separates "what the seed
    required" from "what the league then decided", which are different
    kinds of claim and were previously shown as one list.
    """
    duties = manifest.get("obligations") or {}
    if not duties:
        return {}
    from mironba.report.timeline import name_of

    rows = []
    for entry in duties.get("discharged", []) or []:
        rows.append({
            "team": entry["team"],
            "signed": [
                {"player": name_of(r["player_id"]), "rule": r["rule"],
                 "route": r["route"], "salary": r["salary"]}
                for r in entry.get("signed", []) or []
            ],
            "unmet": entry.get("unmet", []) or [],
        })
    caps = duties.get("hard_caps", {}) or {}
    respected = duties.get("hard_cap_respected", {}) or {}
    return {
        "hard_caps": [{"team": t, "line": line,
                       "respected": respected.get(t)}
                      for t, line in sorted(caps.items())],
        "roster_shortfall": sorted(
            (duties.get("roster_shortfall", {}) or {}).items()),
        "discharged": rows,
        "teams_forced": duties.get("teams_forced", []) or [],
        "findings_seen": sorted(
            (duties.get("findings_seen", {}) or {}).items()),
        "n_unmet": sum(len(r["unmet"]) for r in rows),
    }


def cascade_payoff(manifest: dict) -> dict:
    """What the seed CAUSED, read straight off a run's manifest.

    Every count here is a diff against the same run with the same seed and
    the stipulated trade removed - the null the runner already computes. A
    raw "9 trades generated" is not a result; "4 of those 9 happen only with
    the seed" is. Nothing is recomputed: if the manifest predates the diff,
    the caller gets ``{}`` and the page says the run is too old rather than
    inventing the comparison.
    """
    cascade = manifest.get("cascade") or {}
    if "seeded_trades" not in cascade:
        return {}
    from mironba.report.timeline import name_of

    def names(ids) -> str:
        return ", ".join(name_of(pid) for pid in ids)

    def trade_row(t: dict) -> dict:
        return {
            "acquirer": t.get("acquirer", ""),
            "counterparty": t.get("counterparty", ""),
            "received": names(t.get("received", [])),
            "sent": names(t.get("sent", [])),
            "incoming": t.get("incoming_salary", 0),
            "outgoing": t.get("outgoing_salary", 0),
            "trigger": t.get("trigger", ""),
            "round": t.get("round", 0),
        }

    seeded = cascade.get("seeded_trades", [])
    unseeded = cascade.get("unseeded_trades", [])
    attributable = cascade.get("attributable_to_seed", [])
    displaced = cascade.get("displaced_by_seed", [])

    signings = [
        {"team": row["team"],
         "gained": names(row.get("only_with_seed", [])),
         "lost": names(row.get("only_without_seed", []))}
        for row in cascade.get("signings_changed", []) or []
    ]
    contests = [
        {"player": name_of(row["player_id"]),
         "with_seed": row["with_seed"], "without_seed": row["without_seed"],
         "reason": row.get("reason", ""),
         "arbitrary": "arbitrary" in str(row.get("reason", ""))}
        for row in cascade.get("contests_changed", []) or []
    ]
    # A contested player who moved because a coin-flip landed differently is
    # not evidence of anything the seed did. Counted separately, on the page,
    # every time - not in a footnote.
    arbitrary = sum(1 for c in contests if c["arbitrary"])

    return {
        "n_seeded": len(seeded),
        "n_unseeded": len(unseeded),
        "n_attributable": len(attributable),
        "n_displaced": len(displaced),
        "attributable": [trade_row(t) for t in attributable],
        "displaced": [trade_row(t) for t in displaced],
        "signings": signings,
        "contests": contests,
        "n_contests_arbitrary": arbitrary,
        "n_contests_informative": len(contests) - arbitrary,
        "has_reaction_diff": "signings_changed" in cascade,
        "depth_reached": cascade.get("depth_reached"),
        "killed_by_gate": cascade.get("killed_by_counterparty_gate"),
        "killed_by_solver": cascade.get("killed_by_solver"),
    }
