"""The detail report: everything a run recorded, assembled, no model call.

The narrative report is an LLM call and takes minutes. This is not that. Every
figure below is already in the run's manifest, so assembling it is reading and
formatting - deterministic, instant, and available the moment the run exits.
Nothing here waits on a model, and nothing here is a summary in the sense of
being lossy on purpose: it is the record, arranged.

The one interpretive decision is made explicitly and in one direction. A
contested player resolved by an ARBITRARY tiebreak and one resolved by a
higher offer are different claims. They are counted separately everywhere,
and the arbitrary ones are labelled at the point of display rather than in a
footnote, because a reader scanning "8 contested players went elsewhere" will
otherwise take all eight for signal.
"""

from __future__ import annotations

ARBITRARY_MARK = "arbitrary"


def _names(ids):
    from mironba.report.timeline import name_of

    return [name_of(pid) for pid in ids]


def contested_players(manifest: dict) -> dict:
    """Who was pursued by more than one team, who won, and by what reason."""
    from mironba.report.timeline import name_of

    rows = []
    for contest in manifest.get("contests") or []:
        if not contest.get("contested"):
            continue
        offers = contest.get("offers") or []
        reason = str(contest.get("reason", ""))
        rows.append({
            "player": name_of(contest["player_id"]),
            "player_id": contest["player_id"],
            "winner": contest["winner"],
            "reason": reason,
            "arbitrary": ARBITRARY_MARK in reason.lower(),
            "n_offers": len(offers),
            "offers": offers,
            "losers": [o["team"] for o in offers
                       if o["team"] != contest["winner"]],
        })
    rows.sort(key=lambda r: (-r["n_offers"], r["player"]))
    arbitrary = sum(1 for r in rows if r["arbitrary"])
    return {
        "rows": rows,
        "n": len(rows),
        "n_arbitrary": arbitrary,
        "n_reasoned": len(rows) - arbitrary,
        "uncontested": len(manifest.get("contests") or []) - len(rows),
    }


def per_team(manifest: dict) -> list:
    """Cap position before and after, roster, moves, and routes used."""
    reaction = manifest.get("reaction") or {}
    rows = []
    for team, row in sorted(reaction.items()):
        start = row.get("committed_start") or 0
        end = row.get("committed_end") or 0
        routes = row.get("routes") or []
        by_route: dict = {}
        for entry in routes:
            by_route[entry["route"]] = by_route.get(entry["route"], 0) + 1
        rows.append({
            "team": team,
            "persona": row.get("persona", ""),
            "start": start, "end": end, "delta": end - start,
            "signed": _names(row.get("signed", [])),
            "lost": _names(row.get("lost_contests", [])),
            "cascade": _names(row.get("cascade", [])),
            "routes": [
                {"player": name, "route": entry["route"],
                 "salary": entry["salary"]}
                for name, entry in zip(_names(
                    [e["player_id"] for e in routes]), routes)
            ],
            "route_mix": sorted(by_route.items()),
            "obligations": row.get("obligations", []) or [],
            "unmet": row.get("unmet", []) or [],
            "notes": row.get("notes", []) or [],
            "acted": bool(row.get("signed") or row.get("lost_contests")),
        })
    return rows


def cascade_detail(manifest: dict) -> dict:
    """Where the search stopped, and what stopped it."""
    cascade = manifest.get("cascade") or {}
    if not cascade:
        return {}
    killed_gate = cascade.get("killed_by_counterparty_gate", 0)
    killed_solver = cascade.get("killed_by_solver", 0)
    generated = len(cascade.get("seeded_trades", []))
    considered = generated + killed_gate + killed_solver
    return {
        "depth_reached": cascade.get("depth_reached"),
        "cap_bound": cascade.get("cap_bound"),
        "killed_by_gate": killed_gate,
        "killed_by_solver": killed_solver,
        "generated": generated,
        "considered": considered,
        # Stated as a share so the gates read as the filter they are, not as
        # a failure count. Most candidate pairs SHOULD die here.
        "survival": (generated / considered) if considered else None,
    }


def detail_report(manifest: dict) -> dict:
    """The whole deterministic report, from the manifest alone."""
    if not manifest.get("reaction"):
        return {}
    teams = per_team(manifest)
    return {
        "contested": contested_players(manifest),
        "teams": teams,
        "movers": [t for t in teams if t["acted"]],
        "quiet": [t for t in teams if not t["acted"]],
        "cascade": cascade_detail(manifest),
        "seed_kind": manifest.get("seed_kind", "trade"),
        "model": manifest.get("model"),
        "model_reason": manifest.get("model_reason", ""),
    }
