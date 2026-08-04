"""Player-level deadline ranker: will THIS player be traded at THIS deadline?

    python -m mironba.eval.player_ranker            # missingness + nulls only
    python -m mironba.eval.player_ranker --fit      # leave-one-season-out fit

**The reframe, and why the pair model stays.** The pair ranker's unit is the
team pair: ~71 positives against 435 candidates per season, chronically
underpowered, and its recorded negative (p@10 6.0% vs a 5.01% null - ranking
does not work on features the constraint solver has already consumed) STANDS
UNTOUCHED. This module asks a different question at a different unit - the
player - where ten deadlines supply hundreds of positives instead of dozens.
Both are reported; they answer different questions.

**Features, and where each may be used.**

* ``availability`` - appearances in the player's team's last 10 games before
  the deadline, from the player game logs. This is UNFENCED FOR THE RANKER
  ONLY: the fence test was narrowed, not removed - availability stays out of
  the planner and the value model because nothing about its effect on GM
  behaviour or player value has been validated, while here it is exactly the
  kind of pre-deadline signal a ranker is for, and the ranker's outputs feed
  no simulation.
* ``age`` - from nba_api bio stats (one request per season).
* ``expiring`` - NOT COMPUTABLE FOR ANY SEASON, dropped with the reason:
  the only contract-structure snapshot is forward-looking (retrieved July
  2026), so contracts that ended in 2025-26 had already left the page - no
  player shows final_season 2025-26 - and absence from a forward snapshot
  inversely encodes expiring status through post-deadline outcomes, the
  season+1 leak this module refuses. The test that was meant to pin the
  feature to one season is what exposed this.
* ``log_salary`` - the season's contract row.
* ``team_prior_rate`` - the player's team's deadline-trade rate over PRIOR
  seasons only. This one ENCODES historical trade frequency, so the null
  must absorb it - see below.

**Nulls, stated per number.**

* precision@k: the held-out season's base rate (a random ranking's expected
  precision).
* AUC: 0.5 by construction, and a label-permutation p.
* BOTH also against the WITHIN-TEAM permutation null: labels shuffled only
  among players on the same team-season, which preserves each team's trade
  frequency exactly. A model whose lift survives this null is using more
  than "which teams trade"; ``team_prior_rate`` is fully absorbed by it.

**Missingness is reported by class before any fit** - the artifact channel
that nearly invalidated the pair fit. A feature whose missingness differs
sharply between classes is a leak detector wearing a feature's name.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

SEASONS = tuple(f"{y}-{str(y + 1)[-2:]}" for y in range(2016, 2026))
FEATURES = ("availability", "age", "log_salary", "team_prior_rate")
K = 25


def _deadline(season: str):
    from mironba.world.calendar import CALENDARS

    return CALENDARS[season].deadline


# --------------------------------------------------------------------------
# Labels: traded in the deadline window (Jan 1 .. deadline day), per season
# --------------------------------------------------------------------------


def traded_players(season: str) -> set[str]:
    path = SNAPSHOTS / f"bbref-{season}" / "transactions.csv"
    deadline = _deadline(season).isoformat()
    year = int(season[:4]) + 1
    lo = f"{year}-01-01"
    out: set[str] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("is_trade") != "1":
                continue
            if not (lo <= row["date"] <= deadline):
                continue
            out.update(p for p in row["player_ids"].split("|") if p)
    return out


def roster(season: str) -> dict[str, dict]:
    """player_id -> {team, salary} for everyone under contract that season."""
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            out[row["player_id"]] = {"team": row["team_id"],
                                     "salary": int(row["salary"])}
    return out


# --------------------------------------------------------------------------
# Features
# --------------------------------------------------------------------------


def _bbref_names(season: str) -> dict[str, str]:
    path = SNAPSHOTS / f"bbref-{season}" / "players.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["player_id"]: r["name"] for r in csv.DictReader(handle)}


def _norm(name: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(c for c in text.lower() if c.isalpha())


def availability_by_player(season: str) -> dict[str, float]:
    """Normalised name -> appearances in his team's last 10 pre-deadline games.

    Uses the availability module's machinery - the RANKER-ONLY unfenced
    consumer; the narrowed fence test names this module as the one permitted
    reader outside the display surface.
    """
    from mironba.world.availability import load_player_logs, team_last_games

    logs = load_player_logs(season)
    if not logs:
        return {}
    deadline = _deadline(season)
    windows = {}
    for team in {a.team for a in logs}:
        windows[team] = set(team_last_games(team, deadline, logs, 10))
    counts: dict[str, set] = defaultdict(set)
    for a in logs:
        if a.game_date in windows.get(a.team, ()):
            counts[_norm(a.player_name)].add((a.team, a.game_date))
    return {k: len(v) / 10 for k, v in counts.items()}


def age_by_player(season: str) -> dict[str, float]:
    path = SNAPSHOTS / "nba-stats" / "player_bio.csv"
    if not path.is_file():
        return {}
    out = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["season"] == season and row.get("AGE"):
                out[_norm(row["PLAYER_NAME"])] = float(row["AGE"])
    return out


def expiring_by_player(season: str) -> None:
    """Always None: not computable from the ingest, for ANY season.

    The one structure snapshot (bbref-contracts-2026-27, retrieved July
    2026) is forward-looking - a contract that ended in 2025-26 had already
    left the page, so no player carries final_season 2025-26 - and treating
    absence from that snapshot as "expiring" would leak post-deadline
    outcomes (who re-signed shapes presence). Dropped rather than proxied;
    kept as a function so the reason is greppable where the feature would
    have been."""
    return None


def team_prior_rates(upto_season: str) -> dict[str, float]:
    """Team -> share of its contracted players traded at PRIOR deadlines.

    Strictly earlier seasons only - the frequency feature the within-team
    null must (and does) absorb."""
    traded_count: dict[str, int] = defaultdict(int)
    total: dict[str, int] = defaultdict(int)
    for season in SEASONS:
        if season >= upto_season:
            break
        traded = traded_players(season)
        for pid, row in roster(season).items():
            total[row["team"]] += 1
            traded_count[row["team"]] += pid in traded
    return {t: traded_count[t] / total[t] for t in total if total[t]}


@dataclass
class Row:
    season: str
    player_id: str
    team: str
    label: int
    features: dict


def build_rows() -> list[Row]:
    rows: list[Row] = []
    for season in SEASONS:
        traded = traded_players(season)
        names = _bbref_names(season)
        avail = availability_by_player(season)
        ages = age_by_player(season)
        rates = team_prior_rates(season)
        import math

        for pid, info in roster(season).items():
            key = _norm(names.get(pid, ""))
            rows.append(Row(
                season=season, player_id=pid, team=info["team"],
                label=int(pid in traded),
                features={
                    "availability": avail.get(key),
                    "age": ages.get(key),
                    "log_salary": math.log10(max(info["salary"], 1)),
                    "team_prior_rate": rates.get(info["team"]),
                },
            ))
    return rows


# --------------------------------------------------------------------------
# Pre-fit report: n, class balance, nulls, missingness by class
# --------------------------------------------------------------------------


def prefit_report(rows: list[Row]) -> dict:
    positives = sum(r.label for r in rows)
    report = {
        "n": len(rows), "positives": positives,
        "base_rate": positives / len(rows),
        "null_precision_at_k": positives / len(rows),
        "null_auc": 0.5,
        "missingness": {},
    }
    for feature in FEATURES:
        by_class = {}
        for label in (0, 1):
            group = [r for r in rows if r.label == label]
            missing = sum(1 for r in group if r.features[feature] is None)
            by_class[label] = missing / len(group)
        report["missingness"][feature] = by_class
    return report


def render_prefit(rows: list[Row]) -> dict:
    report = prefit_report(rows)
    print("PLAYER-LEVEL FRAMING - stated before fitting")
    print(f"  unit: (player, deadline).  n = {report['n']} across "
          f"{len(SEASONS)} deadlines")
    print(f"  positives {report['positives']} "
          f"({report['base_rate']:.1%} base rate)")
    print(f"  null precision@{K} = the base rate per held-out season "
          "(random ranking)")
    print("  null AUC = 0.5; permutation p reported with the fit; the "
          "within-team null preserves team trade frequency")
    print("\n  MISSINGNESS BY CLASS (the artifact channel - a leak wears a "
          "feature's name):")
    print(f"  {'feature':<18} {'missing|neg':>12} {'missing|pos':>12}")
    for feature, by_class in report["missingness"].items():
        gap = abs(by_class[0] - by_class[1])
        flag = "  << CLASS-CORRELATED" if gap > 0.10 else ""
        print(f"  {feature:<18} {by_class[0]:>11.1%} {by_class[1]:>11.1%}{flag}")
    return report


# --------------------------------------------------------------------------
# Fit: leave-one-season-out logistic regression
# --------------------------------------------------------------------------


def _matrix(rows: list[Row], medians: dict | None = None):
    import numpy as np

    if medians is None:
        medians = {}
        for feature in FEATURES:
            vals = [r.features[feature] for r in rows
                    if r.features[feature] is not None]
            medians[feature] = float(np.median(vals)) if vals else 0.0
    X = np.array([
        [r.features[f] if r.features[f] is not None else medians[f]
         for f in FEATURES] + [
            1.0 if r.features[f] is None else 0.0 for f in ("availability",
                                                            "age")]
        for r in rows])
    y = np.array([r.label for r in rows])
    return X, y, medians


MISS_COLS = ("availability_missing", "age_missing")


def fit_and_score(rows: list[Row], *, permutations: int = 200,
                  seed: int = 20260204) -> dict:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    rng = random.Random(seed)
    per_season = []
    importances = np.zeros(len(FEATURES) + len(MISS_COLS))
    train_aucs = []

    for held in SEASONS:
        train = [r for r in rows if r.season != held]
        test = [r for r in rows if r.season == held]
        if not any(r.label for r in test):
            continue
        Xtr, ytr, med = _matrix(train)
        Xte, yte, _ = _matrix(test, med)
        scaler = StandardScaler().fit(Xtr)
        model = LogisticRegression(max_iter=2000, class_weight="balanced")
        model.fit(scaler.transform(Xtr), ytr)
        scores = model.predict_proba(scaler.transform(Xte))[:, 1]
        train_aucs.append(roc_auc_score(
            ytr, model.predict_proba(scaler.transform(Xtr))[:, 1]))
        auc = roc_auc_score(yte, scores)
        order = np.argsort(-scores)
        hits = int(sum(yte[order[:K]]))
        base = float(np.mean(yte))

        # within-team permutation: shuffle labels among each team's players,
        # preserving every team-season's trade count exactly
        def within_team_draw():
            shuffled = yte.copy()
            by_team: dict[str, list[int]] = defaultdict(list)
            for i, r in enumerate(test):
                by_team[r.team].append(i)
            for idx in by_team.values():
                vals = [shuffled[i] for i in idx]
                rng.shuffle(vals)
                for i, v in zip(idx, vals):
                    shuffled[i] = v
            return shuffled

        wt_hits, wt_auc_ge = [], 0
        for _ in range(permutations):
            perm = within_team_draw()
            wt_hits.append(int(sum(perm[order[:K]])))
            if len(set(perm)) > 1 and roc_auc_score(perm, scores) >= auc:
                wt_auc_ge += 1
        per_season.append({
            "season": held, "n": len(test), "positives": int(sum(yte)),
            "auc": auc, "p_at_k": hits / K, "null_p_at_k": base,
            "wt_null_p_at_k": float(np.mean(wt_hits)) / K,
            "wt_p_auc": (wt_auc_ge + 1) / (permutations + 1),
        })
        importances += np.abs(model.coef_[0])

    importances /= len(per_season)
    labels = list(FEATURES) + list(MISS_COLS)
    return {
        "per_season": per_season,
        "importance": sorted(zip(labels, importances), key=lambda t: -t[1]),
        "train_auc": float(np.mean(train_aucs)),
    }


def render_fit(result: dict) -> None:
    import numpy as np

    rows = result["per_season"]
    print("\nLEAVE-ONE-SEASON-OUT (fit on 9 deadlines, score the 10th)")
    print(f"  {'season':<8} {'n':>4} {'pos':>4} {'AUC':>6} {'p@25':>7} "
          f"{'null':>6} {'wt-null':>8} {'wt-p(AUC)':>10}")
    for r in rows:
        print(f"  {r['season']:<8} {r['n']:>4} {r['positives']:>4} "
              f"{r['auc']:>6.3f} {r['p_at_k']:>7.1%} {r['null_p_at_k']:>6.1%} "
              f"{r['wt_null_p_at_k']:>8.1%} {r['wt_p_auc']:>10.3f}")
    auc = float(np.mean([r["auc"] for r in rows]))
    p_at_k = float(np.mean([r["p_at_k"] for r in rows]))
    null_p = float(np.mean([r["null_p_at_k"] for r in rows]))
    wt_null = float(np.mean([r["wt_null_p_at_k"] for r in rows]))
    print(f"\n  mean test AUC {auc:.3f} vs 0.5 random null "
          f"(train {result['train_auc']:.3f} - the gap is the overfit read)")
    print(f"  mean p@{K} {p_at_k:.1%}")
    print(f"    vs class-balance null {null_p:.1%}: "
          f"{p_at_k / null_p:.2f}x, headroom "
          f"{(p_at_k - null_p) / (1 - null_p):+.1%} of chance-to-perfect")
    print(f"    vs WITHIN-TEAM null {wt_null:.1%} (preserves team trade "
          f"frequency; absorbs team_prior_rate): {p_at_k / wt_null:.2f}x")
    print("\n  FEATURE IMPORTANCE (|standardised coefficient|, mean over folds):")
    for name, weight in result["importance"]:
        print(f"    {name:<22} {weight:.3f}")


def ablation(rows: list[Row]) -> dict:
    """Salary+team_rate alone versus the full set - the claim's own control.

    'The orthogonal features moved it' is only sayable against the model
    without them; a reduced model at the base rate means they carry the lift.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    def run(feature_set, miss_of):
        per = []
        for held in SEASONS:
            train = [r for r in rows if r.season != held]
            test = [r for r in rows if r.season == held]
            med = {f: float(np.median([r.features[f] for r in train
                                       if r.features[f] is not None] or [0]))
                   for f in feature_set}

            def mat(rs):
                X = np.array(
                    [[r.features[f] if r.features[f] is not None else med[f]
                      for f in feature_set]
                     + [1.0 if r.features[f] is None else 0.0
                        for f in miss_of] for r in rs])
                return X, np.array([r.label for r in rs])

            Xtr, ytr = mat(train)
            Xte, yte = mat(test)
            scaler = StandardScaler().fit(Xtr)
            model = LogisticRegression(max_iter=2000,
                                       class_weight="balanced")
            model.fit(scaler.transform(Xtr), ytr)
            scores = model.predict_proba(scaler.transform(Xte))[:, 1]
            order = np.argsort(-scores)
            per.append((roc_auc_score(yte, scores),
                        float(sum(yte[order[:K]])) / K))
        return (float(np.mean([a for a, _ in per])),
                float(np.mean([p for _, p in per])))

    base_auc, base_p = run(("log_salary", "team_prior_rate"), ())
    full_auc, full_p = run(FEATURES, ("availability", "age"))
    return {"reduced": {"auc": base_auc, "p_at_k": base_p},
            "full": {"auc": full_auc, "p_at_k": full_p}}


BENCH = Path(__file__).resolve().parents[2] / "bench-player-ranker.json"


def main(argv=None) -> int:
    import argparse
    import json
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--permutations", type=int, default=200)
    args = parser.parse_args(argv)

    rows = build_rows()
    prefit = render_prefit(rows)
    if args.fit:
        result = fit_and_score(rows, permutations=args.permutations)
        render_fit(result)
        control = ablation(rows)
        delta_p = control["full"]["p_at_k"] - control["reduced"]["p_at_k"]
        print("\n  ABLATION - the orthogonal-features claim's own control:")
        print(f"    salary + team_rate only : AUC "
              f"{control['reduced']['auc']:.3f}   "
              f"p@{K} {control['reduced']['p_at_k']:.1%}")
        print(f"    + availability/age      : AUC "
              f"{control['full']['auc']:.3f}   "
              f"p@{K} {control['full']['p_at_k']:.1%}   "
              f"(delta {delta_p:+.1%})")
        BENCH.write_text(json.dumps({
            "prefit": prefit, "per_season": result["per_season"],
            "importance": [[n, float(w)] for n, w in result["importance"]],
            "train_auc": result["train_auc"], "ablation": control,
            "k": K, "permutations": args.permutations,
        }, indent=1), encoding="utf-8")
        print(f"\n  wrote {BENCH.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
