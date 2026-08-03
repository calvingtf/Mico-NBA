"""GM personas derived from transaction history. REVEALED DISPOSITION, not
belief modelling - this module says what a front office has DONE, never what
it thinks, wants, or will do. A profile is a summary of observed behaviour
under a named GM; treating it as a mind is exactly the prose-persona failure
the charter banned.

    python -m mironba.models.gm_profile --validate

A profile is a function of ``(team, as_of_date)``:

* the GM comes from a hand-curated, SOURCED tenure table
  (``evidence/league/gm-tenures.csv``) - sourced or absent, never recalled;
  team-seasons before the sourced tenure are UNATTRIBUTABLE and reported;
* parameters are computed from transactions and contracts in seasons
  STRICTLY BEFORE the as-of date, within the GM's tenure - registered in
  DERIVED_FACTS so the freeze discipline covers it;
* a GM with fewer than ``MIN_SEASONS`` attributable pre-date seasons returns
  UNKNOWN and the caller falls back to the league average - reported, never
  silently defaulted.

Parameters computed (each per season, then averaged over tenure seasons):

* ``trade_rate``        - trades the team was party to
* ``aggregation_rate``  - share of its trades sending 2+ players
* ``pick_flow``         - draft-pick mentions received minus sent
* ``retention_rate``    - own players kept season over season, among those
                          still in the league the following season
* ``deadline_share``    - share of its trades landing Jan 1 - Feb 20
* ``posture_agreement`` - of deadline-active seasons, the share where pick
                          direction agrees with the standings disposition
                          (SELLER acquires picks, BUYER spends them)
* ``spend_level``       - payroll over that season's salary cap

NOT COMPUTABLE from the ingested history, dropped with reasons rather than
approximated: average contract length (season tables carry salaries, not
terms) and young-for-veteran direction (no historical age/service ingest).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
TENURES = Path(__file__).resolve().parents[2] / "evidence" / "league" / "gm-tenures.csv"

MIN_SEASONS = 2
UNKNOWN = "UNKNOWN"

PARAMETERS = ("trade_rate", "aggregation_rate", "pick_flow", "retention_rate",
              "deadline_share", "posture_agreement", "spend_level")

SEASONS = tuple(f"{y}-{str(y + 1)[-2:]}" for y in range(2016, 2026))


def load_tenures() -> list[dict]:
    with TENURES.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def gm_for(team: str, season: str, tenures=None) -> str:
    """The sourced GM for a team-season, or '' when unattributable."""
    for row in tenures or load_tenures():
        if row["team_id"] != team:
            continue
        if season >= row["start_season"] and (
                not row["end_season"] or season <= row["end_season"]):
            return row["gm"]
    return ""


def coverage() -> dict:
    tenures = load_tenures()
    attributable = []
    unattributable = []
    for team in sorted({r["team_id"] for r in tenures}):
        for season in SEASONS:
            (attributable if gm_for(team, season, tenures) else
             unattributable).append((team, season))
    return {"attributable": attributable, "unattributable": unattributable,
            "rows": len(tenures)}


# --------------------------------------------------------------------------
# Per-season observations, from snapshot files only
# --------------------------------------------------------------------------


def _team_names() -> dict[str, str]:
    names = {}
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "teams.csv"
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as handle:
                for r in csv.DictReader(handle):
                    names[r["team_id"]] = f"{r['city']} {r['name']}"
    return names


def _transactions(season: str) -> list[dict]:
    path = SNAPSHOTS / f"bbref-{season}" / "transactions.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [r for r in csv.DictReader(handle) if r.get("is_trade") == "1"]


def _outgoing_segment_counts(text: str, team_name: str) -> tuple[int, int]:
    """(players sent, pick mentions sent) read from this team's segments."""
    players = picks = 0
    for segment in text.split(";"):
        if f"the {team_name} traded" not in segment:
            continue
        head = segment.split(" to the ")[0]
        players += len(re.findall(r"\{\{\w+\}\}", head)) or len(
            re.findall(r" [A-Z][a-z]+ [A-Z]", head))
        picks += len(re.findall(r"\d{4} (?:1st|2nd) round draft pick", head))
    return players, picks


@dataclass(frozen=True)
class SeasonObservation:
    season: str
    trades: int
    aggregated: int
    picks_out: int
    picks_in: int
    deadline_trades: int
    deadline_picks_in: int
    deadline_picks_out: int
    retention: float | None
    spend: float | None


def observe(team: str, season: str, names: dict[str, str]) -> SeasonObservation:
    team_name = names.get(team, "")
    trades = aggregated = picks_out = picks_in = 0
    deadline_trades = dpi = dpo = 0
    year = int(season[:4]) + 1
    deadline_lo, deadline_hi = f"{year}-01-01", f"{year}-02-20"
    for row in _transactions(season):
        if team not in row["team_ids"].split("|"):
            continue
        trades += 1
        text = row.get("marked_text") or row["text"]
        sent_players, sent_picks = _outgoing_segment_counts(text, team_name)
        total_picks = len(re.findall(r"\d{4} (?:1st|2nd) round draft pick", text))
        got_picks = max(0, total_picks - sent_picks) if len(
            row["team_ids"].split("|")) == 2 else 0
        if sent_players >= 2:
            aggregated += 1
        picks_out += sent_picks
        picks_in += got_picks
        if deadline_lo <= row["date"] <= deadline_hi:
            deadline_trades += 1
            dpi += got_picks
            dpo += sent_picks

    retention = _retention(team, season)
    spend = _spend(team, season)
    return SeasonObservation(season, trades, aggregated, picks_out, picks_in,
                             deadline_trades, dpi, dpo, retention, spend)


def _roster_ids(team: str, season: str) -> set[str]:
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["player_id"] for r in csv.DictReader(handle)
                if r["team_id"] == team}


def _league_ids(season: str) -> set[str]:
    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as handle:
        return {r["player_id"] for r in csv.DictReader(handle)}


def _retention(team: str, season: str) -> float | None:
    nxt = f"{int(season[:4]) + 1}-{str(int(season[:4]) + 2)[-2:]}"
    mine, everyone_next = _roster_ids(team, season), _league_ids(nxt)
    if not mine or not everyone_next:
        return None
    still = mine & everyone_next
    if not still:
        return None
    return len(_roster_ids(team, nxt) & still) / len(still)


def _spend(team: str, season: str) -> float | None:
    from mironba.rules.constants import environment_for

    path = SNAPSHOTS / f"bbref-{season}" / "contracts.csv"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        payroll = sum(int(r["salary"]) for r in csv.DictReader(handle)
                      if r["team_id"] == team)
    try:
        return payroll / environment_for(season).salary_cap
    except Exception:  # noqa: BLE001 - a season without an env is a gap
        return None


# --------------------------------------------------------------------------
# The profile: (team, as_of) -> parameters, strictly pre-date
# --------------------------------------------------------------------------


def _seasons_before(as_of: date) -> list[str]:
    """Seasons that COMPLETED before as_of - a July date admits the season
    that just ended, an in-season date does not admit the running one."""
    cutoff_start = as_of.year - 1 if as_of.month >= 7 else as_of.year - 2
    return [s for s in SEASONS if int(s[:4]) <= cutoff_start]


@dataclass(frozen=True)
class Profile:
    team: str
    gm: str
    as_of: date
    seasons: tuple[str, ...]
    status: str                      # OK | UNKNOWN
    values: dict


def _posture_agreement(team: str, obs: list[SeasonObservation]) -> float | None:
    from mironba.models.disposition import BUYER, SELLER, disposition

    agree, classified = 0, 0
    for o in obs:
        if not o.deadline_trades:
            continue
        try:
            year = int(o.season[:4]) + 1
            side = disposition(o.season, date(year, 2, 6))[team].side
        except Exception:  # noqa: BLE001 - no standings, no classification
            continue
        if side not in (BUYER, SELLER):
            continue
        classified += 1
        net_picks = o.deadline_picks_in - o.deadline_picks_out
        if side == SELLER and net_picks > 0:
            agree += 1
        elif side == BUYER and net_picks <= 0:
            agree += 1
    return agree / classified if classified else None


def profile(team: str, as_of: date, *, seasons=None, names=None) -> Profile:
    tenures = load_tenures()
    candidates = seasons if seasons is not None else _seasons_before(as_of)
    tenure_seasons = tuple(s for s in candidates if gm_for(team, s, tenures))
    gm = gm_for(team, tenure_seasons[-1], tenures) if tenure_seasons else ""
    # only seasons under THIS gm count toward the profile
    tenure_seasons = tuple(s for s in tenure_seasons
                           if gm_for(team, s, tenures) == gm)
    if len(tenure_seasons) < MIN_SEASONS:
        return Profile(team, gm, as_of, tenure_seasons, UNKNOWN, {})

    names = names or _team_names()
    obs = [observe(team, s, names) for s in tenure_seasons]
    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return mean(vals) if vals else None

    values = {
        "trade_rate": _mean([o.trades for o in obs]),
        "aggregation_rate": _mean(
            [o.aggregated / o.trades for o in obs if o.trades]),
        "pick_flow": _mean([o.picks_in - o.picks_out for o in obs]),
        "retention_rate": _mean([o.retention for o in obs]),
        "deadline_share": _mean(
            [o.deadline_trades / o.trades for o in obs if o.trades]),
        "posture_agreement": _posture_agreement(team, obs),
        "spend_level": _mean([o.spend for o in obs]),
    }
    return Profile(team, gm, as_of, tenure_seasons, "OK", values)


def league_average(as_of: date, teams=None) -> dict:
    """The null profile: every parameter averaged over teams with OK status."""
    teams = teams or sorted({r["team_id"] for r in load_tenures()})
    names = _team_names()
    profiles = [profile(t, as_of, names=names) for t in teams]
    ok = [p for p in profiles if p.status == "OK"]
    out = {}
    for parameter in PARAMETERS:
        vals = [p.values[parameter] for p in ok
                if p.values.get(parameter) is not None]
        out[parameter] = mean(vals) if vals else None
    return out


def to_persona(prof: Profile | None, averages: dict, *,
               validated=(), force_unvalidated: bool = False):
    """Declared mapping into the ONE persona field the reaction consumes.

    Only ``asset_hoarding`` reaches decision logic (via max_assets_out in the
    trade cascade); the other fields are display. The mapping is stated, not
    fitted: observed aggregation above the league average means the GM
    demonstrably ships multi-player packages, so hoarding is LOW.

    VALIDATION GATE: aggregation_rate did NOT beat the league-average null
    out of sample, so unless it appears in ``validated`` (or the caller
    passes ``force_unvalidated=True`` for an explicitly-labelled wiring
    probe), the mapping refuses to differentiate on it and every team gets
    league-average hoarding - a parameter that failed its null must not
    enter the sim as if it had.
    """
    from mironba.agents.gm import GMPersona

    if not force_unvalidated and "aggregation_rate" not in validated:
        label = (f"revealed:{prof.gm} (aggregation failed its null; "
                 "league-average hoarding)") if prof and prof.status == "OK"             else "league-average (UNKNOWN history)"
        return GMPersona(label, risk_tolerance=0.5, win_now_horizon=2,
                         asset_hoarding=0.5)
    if prof is None or prof.status != "OK":
        return GMPersona("league-average (UNKNOWN history)",
                         risk_tolerance=0.5, win_now_horizon=2,
                         asset_hoarding=0.5)
    aggregation = prof.values.get("aggregation_rate")
    baseline = averages.get("aggregation_rate") or 0.0
    if aggregation is None:
        hoarding = 0.5
    elif aggregation > baseline * 1.25:
        hoarding = 0.2
    elif aggregation < baseline * 0.75:
        hoarding = 0.8
    else:
        hoarding = 0.5
    return GMPersona(f"revealed:{prof.gm}", risk_tolerance=0.5,
                     win_now_horizon=2, asset_hoarding=hoarding)


# --------------------------------------------------------------------------
# Out-of-sample validation: early seasons predict held-out later ones?
# --------------------------------------------------------------------------

FIT_SEASONS = SEASONS[:6]      # 2016-17 .. 2021-22
HOLD_SEASONS = SEASONS[6:9]    # 2022-23 .. 2024-25 (2025-26 kept clear of
                               # the live scenarios' freeze)


def validate() -> list[dict]:
    names = _team_names()
    tenures = load_tenures()
    teams = sorted({r["team_id"] for r in tenures})

    fit, held = {}, {}
    for team in teams:
        fit_seasons = tuple(s for s in FIT_SEASONS if gm_for(team, s, tenures))
        hold_seasons = tuple(
            s for s in HOLD_SEASONS
            if gm_for(team, s, tenures)
            and gm_for(team, s, tenures) == (gm_for(team, fit_seasons[-1], tenures)
                                             if fit_seasons else ""))
        if len(fit_seasons) < MIN_SEASONS or len(hold_seasons) < 1:
            continue
        fit[team] = profile(team, date(2022, 8, 1), seasons=fit_seasons,
                            names=names)
        held[team] = profile(team, date(2025, 8, 1), seasons=hold_seasons,
                             names=names)

    null = {}
    for parameter in PARAMETERS:
        vals = [p.values[parameter] for p in fit.values()
                if p.values.get(parameter) is not None]
        null[parameter] = mean(vals) if vals else None

    report = []
    for parameter in PARAMETERS:
        rows = [
            (abs(fit[t].values[parameter] - held[t].values[parameter]),
             abs(null[parameter] - held[t].values[parameter]))
            for t in fit
            if fit[t].values.get(parameter) is not None
            and held[t].values.get(parameter) is not None
            and null[parameter] is not None
        ]
        if not rows:
            report.append({"parameter": parameter, "n": 0})
            continue
        gm_err = mean(r[0] for r in rows)
        null_err = mean(r[1] for r in rows)
        wins = sum(1 for g, n in rows if g < n)
        report.append({
            "parameter": parameter, "n": len(rows),
            "gm_mae": gm_err, "null_mae": null_err, "wins": wins,
            "beats_null": gm_err < null_err,
        })
    return report


def main(argv=None) -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args(argv)

    cov = coverage()
    print(f"GM TENURE TABLE: {cov['rows']} sourced rows (single sourced page "
          "carries no predecessors - earlier team-seasons are unattributable, "
          "not guessed)")
    print(f"  team-seasons attributable   {len(cov['attributable'])}/300")
    print(f"  unattributable              {len(cov['unattributable'])}/300")
    by_team: dict[str, int] = {}
    for team, _ in cov["unattributable"]:
        by_team[team] = by_team.get(team, 0) + 1
    worst = sorted(by_team.items(), key=lambda kv: -kv[1])[:6]
    print("  most unattributable: " + ", ".join(f"{t} ({n})" for t, n in worst))

    if not args.validate:
        return 0

    print("\nOUT-OF-SAMPLE: fit 2016-22 under the same GM, predict 2022-25.")
    print("Null = the league-average fit-window profile. 'Does knowing which "
          "GM it is beat knowing nothing?'")
    print(f"{'parameter':<20} {'n':>3} {'gm mae':>9} {'null mae':>9} "
          f"{'gm wins':>8}  verdict")
    for row in validate():
        if row["n"] == 0:
            print(f"{row['parameter']:<20} {0:>3}  not computable on the "
                  "attributable window")
            continue
        verdict = ("beats the null" if row["beats_null"]
                   else "DOES NOT BEAT THE NULL - do not enter the sim as if "
                        "it had")
        print(f"{row['parameter']:<20} {row['n']:>3} {row['gm_mae']:>9.3f} "
              f"{row['null_mae']:>9.3f} {row['wins']:>5}/{row['n']:<3} {verdict}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
