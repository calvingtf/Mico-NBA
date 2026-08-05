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

**Two corrections of the same leak class (entry #64).** (1) The first fit
assigned each player the team on his season contracts row - and bbref lists
a traded player under his ACQUIRING team, so "zero appearances in his
team's window" partially encoded the label (roster team differed from the
last pre-deadline team for 84-91% of positives vs 8-12% of negatives).
(2) Fixing that by cutting features at the DEADLINE exposed the second:
the label window runs Jan 1..deadline, so a player traded in January
appears for his new team before the deadline and writes his own label into
switched_pre and the team assignment. Features are therefore computed
strictly BEFORE JAN 1 - the label window's own start - from the player's
October-December appearances: team-entering-January, appearance shares in
that team's last 10 pre-January games. The contracts row remains only a
fallback for players with no pre-January appearance, flagged by a column.
The question the model answers is: entering January, will this player be
traded by the deadline?

**Features.**

* ``window_share``  - appearances in team-entering-January's last 10
  pre-January games, as a share of that window (2020-21 started Dec 22, so
  its window is short and the share normalises by its real size). From the
  player game logs - UNFENCED FOR THE RANKER ONLY: the fence test was
  narrowed, not removed; the planner and value model still may not read
  availability.
* ``injured_shaped`` - played for that team earlier in Oct-Dec, then zero
  window appearances.
* ``switched_pre``  - appeared for 2+ teams before Jan 1 (an early-season
  move; January moves are the LABEL and stay out of the features).
* ``never_active``  - no pre-January appearance at all (deep bench /
  two-way / name-join miss share this bucket, and it says so).
* ``age`` - nba_api bio stats; birthdate-derived and season-scoped, so the
  VALUE is freeze-safe. Its MISSINGNESS indicator can flip on post-deadline
  debuts (presence in season bio requires appearing at some point in the
  season); that residual channel is quantified in the pre-fit report.
* ``expiring`` - NOT COMPUTABLE FOR ANY SEASON, dropped with the reason:
  the only contract-structure snapshot is forward-looking (retrieved July
  2026), so contracts that ended in 2025-26 had already left the page - and
  absence from a forward snapshot inversely encodes the label through
  post-deadline outcomes, the season+1 leak this module refuses.
* ``log_salary`` - the season's contract row.
* ``team_prior_rate`` - team-at-deadline's deadline-trade rate over PRIOR
  seasons only. Encodes historical trade frequency, so the null must absorb
  it - see below.

**Nulls, stated per number.**

* precision@k: the held-out season's base rate (a random ranking).
* AUC: 0.5 by construction, plus permutation p.
* BOTH also against the WITHIN-TEAM permutation null: labels shuffled only
  among players sharing a team-at-deadline, preserving each team-season's
  trade count exactly; ``team_prior_rate`` is fully absorbed by it.

**Missingness is reported by class before any fit** - the artifact channel
that produced entry #64's correction in the first place.
"""

from __future__ import annotations

import csv
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

SEASONS = tuple(f"{y}-{str(y + 1)[-2:]}" for y in range(2016, 2026))
FEATURES = ("window_share", "injured_shaped", "switched_pre", "never_active",
            "age", "log_salary", "team_prior_rate")
MISS_COLS = ("age_missing", "team_from_contracts")
K = 25


def _deadline(season: str):
    from mironba.world.calendar import CALENDARS

    return CALENDARS[season].deadline


def _feature_cutoff(season: str):
    """Jan 1 of the deadline year: the label window's own start.

    Every feature is computed from appearances strictly before this date,
    so nothing inside the window it predicts can reach a feature - the
    January-trade leak the deadline cutoff allowed (entry #64)."""
    from datetime import date

    return date(int(season[:4]) + 1, 1, 1)


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
    """player_id -> {team, salary} for everyone under contract that season.

    The team on this row is bbref's season listing - for traded players
    that is the ACQUIRING team, i.e. post-deadline information. It is used
    only as a fallback where no pre-deadline appearance exists, and that
    fallback is flagged (entry #64).
    """
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    out: dict[str, dict] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            out[row["player_id"]] = {"team": row["team_id"],
                                     "salary": int(row["salary"])}
    return out


# --------------------------------------------------------------------------
# Pre-deadline appearance structures (the freeze-computable side)
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


@dataclass(frozen=True)
class PreDeadline:
    """Everything the logs say about a player STRICTLY before Jan 1."""

    last_team: str
    teams: frozenset
    window_games: int          # appearances in last_team's last-10 window
    window_size: int           # actual window size (short in 2020-21)
    played_for_last_team_earlier: bool


def pre_deadline_profiles(season: str) -> dict[str, PreDeadline]:
    """Normalised name -> pre-deadline appearance profile.

    The RANKER-ONLY unfenced consumer of the availability machinery; every
    date compared here is < deadline, and the window itself comes from
    team_last_games, which is strictly-before by construction (tested per
    season)."""
    from mironba.world.availability import load_player_logs, team_last_games

    logs = load_player_logs(season)
    if not logs:
        return {}
    cutoff = _feature_cutoff(season)
    pre = [a for a in logs if a.game_date < cutoff]
    windows = {team: set(team_last_games(team, cutoff, logs, 10))
               for team in {a.team for a in pre}}

    by_player: dict[str, list] = defaultdict(list)
    for a in pre:
        by_player[_norm(a.player_name)].append(a)

    out = {}
    for key, apps in by_player.items():
        apps.sort(key=lambda a: a.game_date)
        last_team = apps[-1].team
        window = windows.get(last_team, set())
        window_games = len({a.game_date for a in apps
                            if a.team == last_team and a.game_date in window})
        earlier = any(a.team == last_team and a.game_date not in window
                      for a in apps)
        out[key] = PreDeadline(
            last_team=last_team,
            teams=frozenset(a.team for a in apps),
            window_games=window_games,
            window_size=len(window),
            played_for_last_team_earlier=earlier,
        )
    return out


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
    team: str                  # team entering January (log-derived)
    label: int
    features: dict


def build_rows() -> list[Row]:
    import math

    rows: list[Row] = []
    for season in SEASONS:
        traded = traded_players(season)
        names = _bbref_names(season)
        profiles = pre_deadline_profiles(season)
        ages = age_by_player(season)
        rates = team_prior_rates(season)

        for pid, info in roster(season).items():
            key = _norm(names.get(pid, ""))
            profile = profiles.get(key)
            if profile is not None:
                team = profile.last_team
                window_share = (profile.window_games
                                / max(profile.window_size, 1))
                injured = float(profile.window_games == 0
                                and profile.played_for_last_team_earlier)
                switched = float(len(profile.teams) >= 2)
                never = 0.0
                from_contracts = 0.0
            else:
                team = info["team"]
                window_share = 0.0
                injured = 0.0
                switched = 0.0
                never = 1.0
                from_contracts = 1.0
            rows.append(Row(
                season=season, player_id=pid, team=team,
                label=int(pid in traded),
                features={
                    "window_share": window_share,
                    "injured_shaped": injured,
                    "switched_pre": switched,
                    "never_active": never,
                    "age": ages.get(key),
                    "log_salary": math.log10(max(info["salary"], 1)),
                    "team_prior_rate": rates.get(team),
                    "_from_contracts": from_contracts,
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
    # the age residual channel: bio presence requires appearing at SOME
    # point in the season, which a deadline observer cannot fully know
    residual = sum(1 for r in rows
                   if r.features["age"] is not None
                   and r.features["never_active"] == 1.0)
    report["age_presence_residual"] = residual
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
    print("  features cut at JAN 1 (the label window's start) and the "
          "team entering January is LOG-DERIVED (entry #64); the "
          "contracts fallback is flagged as a column")
    print(f"  age residual channel: {report['age_presence_residual']} row(s) "
          "carry an age despite zero pre-deadline appearances - bio "
          "presence encodes appeared-at-some-point; bounded and reported")
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
         for f in FEATURES]
        + [1.0 if r.features["age"] is None else 0.0,
           r.features["_from_contracts"]]
        for r in rows])
    y = np.array([r.label for r in rows])
    return X, y, medians


def fit_and_score(rows: list[Row], *, permutations: int = 200,
                  seed: int = 20260204) -> dict:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    rng = random.Random(seed)
    per_season = []
    coef_sum = np.zeros(len(FEATURES) + len(MISS_COLS))
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
        coef_sum += model.coef_[0]

    coef_mean = coef_sum / len(per_season)
    labels = list(FEATURES) + list(MISS_COLS)
    return {
        "per_season": per_season,
        "coefficients": sorted(zip(labels, coef_mean),
                               key=lambda t: -abs(t[1])),
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
    print(f"  PLAIN READING: a randomly chosen traded player outscores a "
          f"randomly chosen\n  untraded one {auc:.0%} of the time (50% would "
          "be a coin flip).")
    print(f"  mean p@{K} {p_at_k:.1%}")
    print(f"    vs class-balance null {null_p:.1%}: "
          f"{p_at_k / null_p:.2f}x, headroom "
          f"{(p_at_k - null_p) / (1 - null_p):+.1%} of chance-to-perfect")
    print(f"    vs WITHIN-TEAM null {wt_null:.1%} (preserves team trade "
          f"frequency; absorbs team_prior_rate): {p_at_k / wt_null:.2f}x")
    print(f"  PLAIN READING: of {K} players flagged per deadline, "
          f"~{p_at_k * K:.1f} are traded,\n  vs ~{null_p * K:.1f} at chance "
          f"and ~{wt_null * K:.1f} under the within-team null.")
    print("\n  MEAN SIGNED COEFFICIENTS (standardised; sign = direction):")
    for name, weight in result["coefficients"]:
        print(f"    {name:<22} {weight:+.3f}")


def ablation(rows: list[Row]) -> dict:
    """Salary+team_rate alone versus the full set - the claim's own control."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    def run(feature_set, extra_cols):
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
                     + [r.features[c] if c != "age_missing"
                        else (1.0 if r.features["age"] is None else 0.0)
                        for c in extra_cols] for r in rs])
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
    full_auc, full_p = run(FEATURES, ("age_missing", "_from_contracts"))
    return {"reduced": {"auc": base_auc, "p_at_k": base_p},
            "full": {"auc": full_auc, "p_at_k": full_p}}


def interaction_test(rows: list[Row], *, permutations: int = 200,
                     seed: int = 20260204) -> dict:
    """Salary x low-minutes as an EXPLICIT term, against the additive form.

    The ablation showed salary+team_rate alone score BELOW the base rate,
    yet log_salary carries the largest coefficient - salary is informative
    only conditioned on playing time, an interaction the additive model
    reconstructs through its main effects. This fits both forms on the same
    folds and the same nulls. The term:

        interaction = log_salary x (1 - window_share) x (1 - never_active)

    - "paid but not playing", among players who actually appeared before
    January (never-actives are a different population and keep their own
    main effect). The recorded result stays entry #64's additive model
    unless this beats it against the same nulls.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.preprocessing import StandardScaler

    def matrices(rs, med, with_inter):
        X = []
        for r in rs:
            base = [r.features[f] if r.features[f] is not None else med[f]
                    for f in FEATURES]
            extra = [1.0 if r.features["age"] is None else 0.0,
                     r.features["_from_contracts"]]
            if with_inter:
                inter = (r.features["log_salary"]
                         * (1 - r.features["window_share"])
                         * (1 - r.features["never_active"]))
                extra.append(inter)
            X.append(base + extra)
        return np.array(X), np.array([r.label for r in rs])

    rng = random.Random(seed)
    out = {"additive": [], "interaction": []}
    coef = {"additive": [], "interaction": []}
    for held in SEASONS:
        train = [r for r in rows if r.season != held]
        test = [r for r in rows if r.season == held]
        med = {f: float(np.median([r.features[f] for r in train
                                   if r.features[f] is not None] or [0]))
               for f in FEATURES}
        for name, with_inter in (("additive", False), ("interaction", True)):
            Xtr, ytr = matrices(train, med, with_inter)
            Xte, yte = matrices(test, med, with_inter)
            scaler = StandardScaler().fit(Xtr)
            model = LogisticRegression(max_iter=2000,
                                       class_weight="balanced")
            model.fit(scaler.transform(Xtr), ytr)
            scores = model.predict_proba(scaler.transform(Xte))[:, 1]
            order = np.argsort(-scores)
            auc = roc_auc_score(yte, scores)

            by_team: dict[str, list[int]] = defaultdict(list)
            for i, r in enumerate(test):
                by_team[r.team].append(i)
            wt_hits, wt_auc_ge = [], 0
            for _ in range(permutations):
                perm = yte.copy()
                for idx in by_team.values():
                    vals = [perm[i] for i in idx]
                    rng.shuffle(vals)
                    for i, v in zip(idx, vals):
                        perm[i] = v
                wt_hits.append(int(sum(perm[order[:K]])))
                if len(set(perm)) > 1 and roc_auc_score(perm, scores) >= auc:
                    wt_auc_ge += 1
            out[name].append({
                "auc": auc, "p_at_k": float(sum(yte[order[:K]])) / K,
                "null_p_at_k": float(np.mean(yte)),
                "wt_null_p_at_k": float(np.mean(wt_hits)) / K,
                "wt_p_auc": (wt_auc_ge + 1) / (permutations + 1),
            })
            coef[name].append(model.coef_[0])

    def summary(name):
        rows_ = out[name]
        return {k: float(np.mean([r[k] for r in rows_]))
                for k in ("auc", "p_at_k", "null_p_at_k", "wt_null_p_at_k")}

    labels = list(FEATURES) + list(MISS_COLS)
    add_coef = np.mean(coef["additive"], axis=0)
    int_coef = np.mean(coef["interaction"], axis=0)
    return {
        "additive": summary("additive"),
        "interaction": summary("interaction"),
        "coef_shift": {
            "log_salary": {"additive": float(add_coef[labels.index("log_salary")]),
                           "interaction": float(int_coef[labels.index("log_salary")])},
            "window_share": {"additive": float(add_coef[labels.index("window_share")]),
                             "interaction": float(int_coef[labels.index("window_share")])},
            "salary_x_low_minutes": float(int_coef[-1]),
        },
    }


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
        print(f"    + appearance components : AUC "
              f"{control['full']['auc']:.3f}   "
              f"p@{K} {control['full']['p_at_k']:.1%}   "
              f"(delta {delta_p:+.1%})")
        inter = interaction_test(rows, permutations=args.permutations)
        a, b = inter["additive"], inter["interaction"]
        shift = inter["coef_shift"]
        print("\n  INTERACTION TEST - salary x low-minutes as an explicit term:")
        print(f"    additive    : AUC {a['auc']:.3f}   p@{K} {a['p_at_k']:.1%}"
              f"   (wt-null {a['wt_null_p_at_k']:.1%})")
        print(f"    +interaction: AUC {b['auc']:.3f}   p@{K} {b['p_at_k']:.1%}"
              f"   (wt-null {b['wt_null_p_at_k']:.1%})")
        print(f"    main effects under the term: log_salary "
              f"{shift['log_salary']['additive']:+.3f} -> "
              f"{shift['log_salary']['interaction']:+.3f}, window_share "
              f"{shift['window_share']['additive']:+.3f} -> "
              f"{shift['window_share']['interaction']:+.3f}; "
              f"term itself {shift['salary_x_low_minutes']:+.3f}")
        improves = (b["p_at_k"] > a["p_at_k"]) and (b["auc"] > a["auc"])
        if improves:
            print("    the explicit term improves both metrics; #64's "
                  "additive model stays the recorded result unless this "
                  "holds against the same nulls on re-examination")
        else:
            print("    the explicit term does NOT improve the fit - the "
                  "additive form was already sufficient, which is worth "
                  "knowing; #64 stands")
        BENCH.write_text(json.dumps({
            "prefit": prefit, "per_season": result["per_season"],
            "interaction_test": inter,
            "coefficients": [[n, float(w)] for n, w in result["coefficients"]],
            "train_auc": result["train_auc"], "ablation": control,
            "k": K, "permutations": args.permutations,
            "team_assignment": "log-derived, features cut at Jan 1 "
                               "(entry #64: two leaks, one class)",
        }, indent=1), encoding="utf-8")
        print(f"\n  wrote {BENCH.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
