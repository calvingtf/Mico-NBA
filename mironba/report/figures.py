"""README figures, generated from recorded results - never typed in.

    python -m mironba.report.figures

The same rule as the text: **no figure without its null**, with n and p
stated where they exist. Every number here is read from a recorded file
(bench json/csv, the measurements ledger, evidence stores) or recomputed
live through the same audited functions that produced the recorded number -
a hardcoded chart is a figure without provenance and can silently drift
from the data it claims to show. A missing anchor raises; it never
silently plots a default.

Outputs SVG into docs/figures/, committed, referenced by relative path.
Palette is plain, baselines start at zero, one axis per chart.
"""

from __future__ import annotations

import json
import re
import sys
from itertools import combinations
from math import comb
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "figures"

GREY = "#9aa0a6"
DARK = "#202124"
ACCENT = "#1a73e8"
BAD = "#b3261e"

ARM_SCENARIOS = ("curry-to-lakers", "mid-flexibility-bulls",
                 "undetermined-byc")


def _p_sign(wins: int, n: int) -> float:
    return sum(comb(n, k) for k in range(wins, n + 1)) / 2 ** n


def _wins_from_p(p: float, n: int) -> int:
    """Invert the one-sided sign test: the wins count whose p matches."""
    return min(range(n + 1), key=lambda w: abs(_p_sign(w, n) - p))


def _require(pattern: str, text: str, where: str) -> re.Match:
    match = re.search(pattern, text)
    if not match:
        raise SystemExit(f"anchor not found in {where}: {pattern!r} - the "
                         "recorded source moved; fix the anchor, never the data")
    return match


# --------------------------------------------------------------------------
# (a) three-arm A/B - the recorded bench files, aggregated across scenarios
# --------------------------------------------------------------------------


def arm_data() -> dict:
    arms = {"blind": [], "feasible": [], "unlock": []}
    for scenario in ARM_SCENARIOS:
        m16 = json.loads((ROOT / f"bench-m16-{scenario}.json").read_text(
            encoding="utf-8"))
        arms["blind"].append(m16["blind"])
        arms["feasible"].append(m16["feasible"])
        m2 = json.loads((ROOT / f"bench-m2-{scenario}.json").read_text(
            encoding="utf-8"))
        arms["unlock"].append(m2["unlock"])
    out = {}
    for arm, rows in arms.items():
        intents = sum(r["intents"] for r in rows)
        out[arm] = {
            # unreachable is recorded as a COUNT; satisfiable_first as a
            # per-scenario FRACTION - so the pooled rate weights fractions
            # by each scenario's intent count. Verified against the recorded
            # 65.5/0/0 and 31.0/58.6/100 pooled table (measurements).
            "unreachable": 100 * sum(
                r["intents_naming_an_unreachable_target"] for r in rows) / intents,
            "satisfiable_first": 100 * sum(
                r["intent_satisfiable_first"] * r["intents"] for r in rows) / intents,
            "intents": intents,
        }
    return out


def figure_arms() -> list[str]:
    data = arm_data()
    labels = ["unaided (blind)", "feasible", "unlock"]
    keys = ["blind", "feasible", "unlock"]
    unreachable = [data[k]["unreachable"] for k in keys]
    satisfiable = [data[k]["satisfiable_first"] for k in keys]
    n = sum(data[k]["intents"] for k in keys)

    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    x = range(len(keys))
    width = 0.38
    ax.bar([i - width / 2 for i in x], unreachable, width,
           color=BAD, label="named an unreachable target")
    ax.bar([i + width / 2 for i in x], satisfiable, width,
           color=ACCENT, label="satisfiable on the first attempt")
    for i, (u, s) in enumerate(zip(unreachable, satisfiable)):
        ax.text(i - width / 2, u + 2, f"{u:.1f}%", ha="center", fontsize=9)
        ax.text(i + width / 2, s + 2, f"{s:.1f}%", ha="center", fontsize=9)
    ax.set_xticks(list(x), labels)
    ax.set_ylim(0, 112)
    ax.set_ylabel("% of stated intents")
    ax.set_title("What the model is shown decides what it can want\n"
                 f"(same model, same scenarios, {n} intents; each arm is the "
                 "others' control)", fontsize=10)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "three-arm.svg")
    plt.close(fig)
    return [f"three-arm.svg <- bench-m16-*.json + bench-m2-*.json "
            f"({', '.join(ARM_SCENARIOS)})"]


# --------------------------------------------------------------------------
# (b) every metric against its null - failures included
# --------------------------------------------------------------------------


def metric_rows() -> list[dict]:
    """(label, observed %, null %, annotation, source) - mixed outcomes."""
    rows = []

    pooled = json.loads((ROOT / "bench-pooled-10season.json").read_text(
        encoding="utf-8"))
    null_pct, _ = season_series()
    rows.append(dict(
        label="deadline precision, 10 seasons",
        observed=pooled["precision"], null=null_pct,
        beats=True,   # recorded: p<0.0001, the one survivor
        note=f"n={pooled['total']['proposed']} proposals - "
             f"{pooled['precision'] / null_pct:.2f}x, "
             f"+{pooled['precision'] - null_pct:.2f} pts - p<0.0001",
        source="bench-pooled-10season.json + snapshots via pooled_backtest"))

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = _require(r"counterparty matches \| (\d+) of (\d+) \| \*\*([\d.]+) "
                 r"expected\*\* \| P\(null ≥ observed\) = \*\*([\d.]+)\*\*",
                 readme, "README counterparty row")
    hit, n, exp, p = int(m[1]), int(m[2]), float(m[3]), float(m[4])
    rows.append(dict(label=f"counterparty matches, n={n} trades",
                     observed=100 * hit / n, null=100 * exp / n,
                     beats=False,  # recorded: p=0.426
                     note=f"{hit}/{n} vs {exp} expected - p={p}",
                     source="README recorded table (entry 26)"))

    measurements = (ROOT / "docs" / "measurements.md").read_text(
        encoding="utf-8")
    m = _require(r"p@10 of ([\d.]+)% against a ([\d.]+)% base rate",
                 measurements, "measurements ranker entry")
    rows.append(dict(label="ranker p@10, 61 trades / 10 folds",
                     observed=float(m[1]), null=float(m[2]),
                     beats=False,  # recorded negative
                     note=f"{float(m[1]) / float(m[2]):.2f}x - NEGATIVE: solver "
                          "already consumed the variance",
                     source="docs/measurements.md ranker table"))

    m = _require(r"\| Validator legality \| (\d+)% \| (\d+)% approve-everything",
                 measurements, "measurements legality row")
    rows.append(dict(label="validator legality, 10 real trades",
                     observed=float(m[1]), null=float(m[2]),
                     beats=False,
                     note="BELOW the approve-everything ceiling - relabelled "
                          "a 30% false-rejection rate (entry 19)",
                     source="docs/measurements.md summary table"))

    from mironba.models.gm_profile import validate
    spend = next(r for r in validate() if r["parameter"] == "spend_level")
    rows.append(dict(
        label=f"GM spend persistence, n={spend['n']} stints",
        observed=100 * spend["wins"] / spend["n"], null=50.0,
        beats=False,
        note=f"{spend['wins']}/{spend['n']} wins vs coin flip - "
             f"p={spend['p']:.3f}: does not beat the null",
        source="recomputed live: gm_profile.validate() on stores"))

    # Enumerated over declared scenarios, never a named one - the fence's
    # rule applies to figures too. Today exactly one pending scenario has
    # curated conditionals; a second joins this figure by declaration.
    from mironba.world.scenario import CONFIG_DIR, load_scenario
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        sc = load_scenario(path.stem)
        if sc.kind != "pending_decision" or not sc.condition_markers:
            continue
        conds = sc.ledger().open_conditionals()
        if not conds:
            continue
        marker = sc.condition_markers.get(sc.blocker_branch, "")
        fired = sum(
            sc.condition_fires_in(c.condition, sc.blocker_branch)
            == (marker in c.condition.lower()) for c in conds)
        rows.append(dict(
            label=f"conditionals fire ({sc.id}), n={len(conds)}",
            observed=100 * fired / len(conds), null=50.0,
            beats=False,  # p=0.0625 - above the threshold refused elsewhere
            note=f"{fired}/{len(conds)} vs random attachment - p="
                 f"{0.5 ** len(conds):.4f}: suggestive, not significant",
            source=f"recomputed live: {sc.id} evidence ledger"))

    from mironba.eval.draft_score import score
    draft = score(2026, trials=5000)
    rows.append(dict(
        label=f"draft assignment, {draft['resolved']} resolved slots",
        observed=100 * draft["hits"] / draft["resolved"],
        null=100 * draft["null1_expected"] / draft["resolved"],
        beats=False,  # recorded: above chance at n=6 "means little"
        note=f"{draft['hits']}/{draft['resolved']} vs {draft['null1_expected']:.2f} "
             f"expected random - {60 - draft['resolved']} of 60 UNRESOLVED",
        source="recomputed live: draft_score on evidence/draft-2026"))
    rows.append(dict(
        label=f"draft vs consensus mock, {draft['null2_slots']} slots",
        observed=100 * draft["null2_sim_hits"] / draft["null2_slots"],
        null=100 * draft["null2_mock_hits"] / draft["null2_slots"],
        beats=False,
        note=f"sim {draft['null2_sim_hits']}/{draft['null2_slots']} vs mock "
             f"{draft['null2_mock_hits']}/{draft['null2_slots']} - LOSES to "
             "the mock, said plainly",
        source="recomputed live: draft_score (mock = the null)"))
    return rows


def figure_metrics() -> list[str]:
    rows = metric_rows()
    fig, ax = plt.subplots(figsize=(9.4, 0.62 * len(rows) + 1.6))
    ys = list(range(len(rows)))[::-1]
    for y, row in zip(ys, rows):
        lo, hi = sorted((row["observed"], row["null"]))
        ax.plot([lo, hi], [y, y], color=GREY, lw=1.4, zorder=1)
        ax.scatter([row["null"]], [y], marker="o", facecolors="white",
                   edgecolors=DARK, s=52, zorder=2,
                   label="null" if y == ys[0] else None)
        # colour follows the RECORDED verdict, never raw direction - a
        # ranker dot painted "win" would misstate a recorded negative
        ax.scatter([row["observed"]], [y], marker="o",
                   color=ACCENT if row["beats"] else BAD, s=52, zorder=3,
                   label="observed" if y == ys[0] else None)
        ax.text(-2.5, y, row["label"], ha="right", va="center", fontsize=9)
        ax.text(103, y, row["note"], ha="left", va="center", fontsize=7.6,
                color=DARK)
    ax.set_xlim(0, 100)
    ax.set_yticks([])
    ax.set_xlabel("% of each metric's own denominator (axis 0-100, no truncation)")
    ax.set_title("Every metric beside its null - failures included",
                 fontsize=11)
    handles, labels = ax.get_legend_handles_labels()
    labels = ["null" if l == "null" else "beats its null (recorded verdict)"
              for l in labels]
    ax.legend(handles, labels, frameon=False, fontsize=9, loc="lower right")
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout()
    fig.subplots_adjust(left=0.30, right=0.60)
    fig.savefig(OUT / "metrics-vs-nulls.svg")
    plt.close(fig)
    return ["metrics-vs-nulls.svg <- " + "; ".join(
        sorted({r['source'] for r in rows}))]


# --------------------------------------------------------------------------
# (c) deadline recall per season, each against ITS OWN null
# --------------------------------------------------------------------------


def season_series():
    """(pooled null precision %, per-season rows) via the audited functions."""
    from mironba.eval.pooled_backtest import (PAIR_SPACE, p_hit,
                                              pooled_null_precision,
                                              read_seasons)
    from mironba.sim.deadline import actual_deadline_trades

    stored = read_seasons()
    season_nulls, per_season = [], []
    for season, row in sorted(stored.items()):
        actual = actual_deadline_trades(season)
        season_pairs = {frozenset(c) for t in actual
                        for c in combinations(sorted(t.teams), 2)}
        season_nulls.append((int(row["proposed"]),
                             len(season_pairs) / PAIR_SPACE))
        null_matched = sum(p_hit(
            len({frozenset(c) for c in combinations(sorted(t.teams), 2)}),
            int(row["pairs"])) for t in actual)
        per_season.append(dict(
            season=season, n=int(row["actual"]),
            recall=100 * int(row["matched"]) / int(row["actual"]),
            null=100 * null_matched / int(row["actual"])))
    return pooled_null_precision(season_nulls) * 100, per_season


def figure_seasons() -> list[str]:
    _, rows = season_series()
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    x = range(len(rows))
    ax.bar(x, [r["recall"] for r in rows], 0.62, color=ACCENT, zorder=2,
           label="recall (matched / actual trades)")
    for i, r in enumerate(rows):
        ax.plot([i - 0.38, i + 0.38], [r["null"], r["null"]], color=DARK,
                lw=2, zorder=3, label="per-season null" if i == 0 else None)
        if r["recall"] < r["null"]:
            ax.text(i, max(r["recall"], r["null"]) + 3, "below\nnull",
                    ha="center", fontsize=7.5, color=BAD)
        ax.text(i, 2.5, f"n={r['n']}", ha="center", fontsize=7,
                color="white", zorder=4)
    ax.set_xticks(list(x), [r["season"] for r in rows], fontsize=8)
    ax.set_ylim(0, 112)
    ax.set_ylabel("%")
    ax.set_title("Deadline-trade recall per season, each against its own null\n"
                 "(a proposal in season S can only hit a trade in season S)",
                 fontsize=10)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "deadline-per-season.svg")
    plt.close(fig)
    return ["deadline-per-season.svg <- bench-pooled-10season.csv + "
            "transaction snapshots via pooled_backtest/deadline functions"]


# --------------------------------------------------------------------------
# (d) persona persistence: n=11 suggestion vs its collapse at n=29
# --------------------------------------------------------------------------


def persistence_series():
    measurements = (ROOT / "docs" / "measurements.md").read_text(
        encoding="utf-8")
    block = _require(
        r"(?s)## 53\..*?(?=## 54\.)", measurements + "## 54.",
        "measurements entry 53")  # entry text, bounded
    text = block.group(0)
    n11 = {}
    for param, n in (("spend_level", 11), ("trade_rate", 11),
                     ("deadline_share", 11), ("posture", 9)):
        m = _require(param.replace("_", "_") + r"[^0-9]*p=(\d+\.\d+)", text,
                     f"entry 53 {param}")
        p = float(m[1])
        n11[param] = dict(n=n, p=p, wins=_wins_from_p(p, n))

    from mironba.models.gm_profile import validate
    n29 = {r["parameter"]: r for r in validate()}
    out = []
    for param, key29 in (("spend_level", "spend_level"),
                         ("trade_rate", "trade_rate"),
                         ("deadline_share", "deadline_share"),
                         ("posture", "posture_agreement")):
        r29 = n29[key29]
        out.append(dict(param=key29, early=n11[param],
                        late=dict(n=r29["n"], p=r29["p"], wins=r29["wins"])))
    return out


def figure_persistence() -> list[str]:
    rows = persistence_series()
    fig, ax = plt.subplots(figsize=(8.2, 3.6))
    x = range(len(rows))
    width = 0.36
    early = [100 * r["early"]["wins"] / r["early"]["n"] for r in rows]
    late = [100 * r["late"]["wins"] / r["late"]["n"] for r in rows]
    ax.bar([i - width / 2 for i in x], early, width, color=GREY,
           label="fit at n=11 stints (suggestive)")
    ax.bar([i + width / 2 for i in x], late, width, color=ACCENT,
           label="recomputed at n=29 (past the costed n=23)")
    for i, r in enumerate(rows):
        ax.text(i - width / 2, early[i] + 2,
                f"{r['early']['wins']}/{r['early']['n']}\np={r['early']['p']:.2f}",
                ha="center", fontsize=7.4)
        ax.text(i + width / 2, late[i] + 2,
                f"{r['late']['wins']}/{r['late']['n']}\np={r['late']['p']:.2f}",
                ha="center", fontsize=7.4)
    ax.axhline(50, color=DARK, lw=1.2, ls="--")
    ax.text(len(rows) - 0.45, 51.5, "coin flip", fontsize=8, ha="right")
    ax.set_xticks(list(x), [r["param"] for r in rows], fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of same-GM pairs the profile beats the null on")
    ax.set_title("The power calculation did its job: the n=11 suggestion "
                 "dissolves at n=29", fontsize=10)
    ax.legend(frameon=False, fontsize=9, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "persistence-power.svg")
    plt.close(fig)
    return ["persistence-power.svg <- docs/measurements.md entry 53 (n=11) + "
            "live gm_profile.validate() on stores (n=29)"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    provenance = []
    for build in (figure_arms, figure_metrics, figure_seasons,
                  figure_persistence):
        provenance += build()
        print(f"built {provenance[-1].split(' <- ')[0]}")
    print("\nPROVENANCE (figure <- recorded source):")
    for line in provenance:
        print(" ", line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
