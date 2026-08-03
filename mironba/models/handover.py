"""The handover test: does behaviour shift when the PERSON changes?

    python -m mironba.models.handover

Same-GM persistence cannot distinguish "this person spends this way" from
"this franchise spends this way" - both predict the team's future from the
team's past. A GM change can: if a parameter is person-driven it should
SHIFT at a handover, and if it is franchise-driven it should stay FLAT.

**Design, declared before running.** A clean handover is a sourced
lead-to-lead succession with at least ``MIN_SIDE`` attributable seasons on
each side (LAC and NYK are excluded by their tenure-table notes: the sourced
authority did not change cleanly). For each handover and parameter:

* shift  = |mean over the first 3 post seasons - mean over the last 3 pre|
* drift  = the mean adjacent-season |delta| WITHIN the two stints - the same
  team's ordinary year-to-year movement under a constant decider
* the handover counts as a SHIFT when shift > drift; the sign test across
  handovers asks whether shifts outnumber coin-flip expectation.

**The residual confound, stated here and in the README:** a handover is not
clean either - new GMs often arrive with a mandate, so a shift can be the
situation rather than the person. This design narrows the person-vs-
franchise confound; it does not remove it. A FLAT result is the stronger
reading: flat survives the confound (a mandate would inflate shifts, not
suppress them), while a SHIFT result would still be person-or-mandate.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from math import comb
from pathlib import Path
from statistics import mean

from mironba.models.gm_profile import (
    PARAMETERS,
    SEASONS,
    _team_names,
    gm_for,
    load_tenures,
    observe,
)

MIN_SIDE = 2
SIDE_WINDOW = 3

#: Handovers excluded because the sourced tenure notes say the authority did
#: not change cleanly (title change under a continuing lead, or a president
#: appointed mid-stint). Read from the table's own notes, not re-decided.
UNCLEAN_MARKERS = ("excluded from clean handovers", "handover excluded",
                   "excluded from the clean set")


def _season_values(team: str, seasons, names) -> dict[str, list]:
    out: dict[str, list] = {p: [] for p in PARAMETERS}
    for s in seasons:
        o = observe(team, s, names)
        out["trade_rate"].append(o.trades)
        out["aggregation_rate"].append(o.aggregated / o.trades if o.trades else None)
        out["pick_flow"].append(o.picks_in - o.picks_out)
        out["retention_rate"].append(o.retention)
        out["deadline_share"].append(o.deadline_trades / o.trades if o.trades else None)
        out["posture_agreement"].append(None)   # season-level posture is 0/1-ish; skip
        out["spend_level"].append(o.spend)
    return out


@dataclass(frozen=True)
class Handover:
    team: str
    before_gm: str
    after_gm: str
    boundary: str          # first season of the successor
    pre_seasons: tuple
    post_seasons: tuple


def clean_handovers() -> list[Handover]:
    tenures = load_tenures()
    unclean_teams = {
        r["team_id"] for r in tenures
        if any(m in r.get("note", "") for m in UNCLEAN_MARKERS)
    }
    out = []
    for team in sorted({r["team_id"] for r in tenures}):
        if team in unclean_teams:
            continue
        timeline = [(s, gm_for(team, s, tenures)) for s in SEASONS]
        for i in range(1, len(timeline)):
            season_now, gm_now = timeline[i]
            _, gm_prev = timeline[i - 1]
            if not gm_now or not gm_prev or gm_now == gm_prev:
                continue
            pre = tuple(s for s, g in timeline[:i] if g == gm_prev)[-SIDE_WINDOW:]
            post = tuple(s for s, g in timeline[i:] if g == gm_now)[:SIDE_WINDOW]
            if len(pre) >= MIN_SIDE and len(post) >= MIN_SIDE:
                out.append(Handover(team, gm_prev, gm_now, season_now, pre, post))
    return out


def handover_test() -> dict:
    names = _team_names()
    handovers = clean_handovers()
    per_parameter: dict[str, list] = {p: [] for p in PARAMETERS}

    for h in handovers:
        pre_vals = _season_values(h.team, h.pre_seasons, names)
        post_vals = _season_values(h.team, h.post_seasons, names)
        for parameter in PARAMETERS:
            pre = [v for v in pre_vals[parameter] if v is not None]
            post = [v for v in post_vals[parameter] if v is not None]
            if len(pre) < MIN_SIDE or len(post) < MIN_SIDE:
                continue
            shift = abs(mean(post) - mean(pre))
            drifts = [abs(b - a) for series in (pre, post)
                      for a, b in zip(series, series[1:])]
            if not drifts:
                continue
            drift = mean(drifts)
            per_parameter[parameter].append(
                {"team": h.team, "boundary": h.boundary, "shift": shift,
                 "drift": drift, "shifted": shift > drift})

    report = {}
    for parameter, rows in per_parameter.items():
        n = len(rows)
        if n == 0:
            report[parameter] = {"n": 0}
            continue
        shifted = sum(r["shifted"] for r in rows)
        p = sum(comb(n, k) for k in range(shifted, n + 1)) / 2 ** n
        report[parameter] = {"n": n, "shifted": shifted, "p": p, "rows": rows}
    return {"handovers": handovers, "report": report}


def main(argv=None) -> int:
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    result = handover_test()
    handovers = result["handovers"]
    print(f"CLEAN HANDOVERS: n = {len(handovers)} - stated before any result")
    for h in handovers:
        print(f"  {h.team}  {h.before_gm} -> {h.after_gm}  at {h.boundary}  "
              f"(pre {len(h.pre_seasons)}, post {len(h.post_seasons)})")

    print(f"\n{'parameter':<20} {'n':>3} {'shifted':>8} {'p(sign)':>8}  reading")
    for parameter in PARAMETERS:
        row = result["report"][parameter]
        if row["n"] == 0:
            print(f"{parameter:<20} {0:>3}  not computable at season level")
            continue
        if row["p"] <= 0.05:
            reading = "SHIFTS at handovers - person-or-mandate (confound stated)"
        elif row["shifted"] <= row["n"] / 2:
            reading = ("FLAT - shifts do not exceed within-stint drift; "
                       "franchise-or-noise, NOT a GM disposition")
        else:
            reading = "leans shift, not separable from drift at this n"
        print(f"{parameter:<20} {row['n']:>3} {row['shifted']:>4}/{row['n']:<3} "
              f"{row['p']:>8.3f}  {reading}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
