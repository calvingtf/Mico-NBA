"""Scenario seeds: the counterfactual, and everything needed to stage it.

A scenario names two teams, a date, a snapshot, and the persona of the GM being
simulated. It also declares how base-year compensation is resolved, because the
snapshot cannot answer that and the honest options are "assume" or "leave
undetermined" — both of which have to be recorded rather than defaulted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import yaml

from mironba.agents.gm import GMContext, GMPersona, RosterEntry
from mironba.data.db import connect, initialize
from mironba.data.loader import load_snapshot
from mironba.rules.cap import tier_for_salary
from mironba.rules.constants import environment_for
from mironba.rules.solver import Asset, TargetScan, scan_targets
from mironba.rules.trade_validator import TeamTradeState
from mironba.sim.boundary import BYCResolution

SNAPSHOT_ROOT = Path(__file__).resolve().parents[1] / "data" / "snapshots"
SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "configs" / "scenario"


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    season: str
    snapshot: str
    team: str
    partner: str
    trade_date: date
    seed: str
    persona: GMPersona
    byc: BYCResolution
    roster_size: int = 15
    roster_count: int = 15
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def snapshot_dir(self) -> Path:
        return SNAPSHOT_ROOT / self.snapshot


def load_scenario(path: Path | str) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    missing = [
        k for k in ("id", "season", "snapshot", "team", "partner", "trade_date", "seed")
        if k not in raw
    ]
    if missing:
        raise ScenarioError(f"{path}: missing {', '.join(missing)}")

    persona_raw = dict(raw.get("persona", {}) or {})
    persona = GMPersona(
        label=persona_raw.get("label", "unnamed"),
        risk_tolerance=float(persona_raw.get("risk_tolerance", 0.5)),
        win_now_horizon=int(persona_raw.get("win_now_horizon", 3)),
        asset_hoarding=float(persona_raw.get("asset_hoarding", 0.5)),
    )
    persona.validate()

    byc_raw = dict(raw.get("byc_resolution", {}) or {})
    byc = BYCResolution(
        mode=byc_raw.get("mode", "unresolved"),
        sourced=bool(byc_raw.get("sourced", False)),
        note=byc_raw.get("note", ""),
    )
    if byc.mode not in {"unresolved", "assume_not_re_signed"}:
        raise ScenarioError(
            f"{path}: byc_resolution.mode must be 'unresolved' or "
            f"'assume_not_re_signed', got {byc.mode!r}"
        )

    return Scenario(
        id=raw["id"],
        season=str(raw["season"]),
        snapshot=raw["snapshot"],
        team=raw["team"].upper(),
        partner=raw["partner"].upper(),
        trade_date=date.fromisoformat(str(raw["trade_date"])),
        seed=raw["seed"].strip(),
        persona=persona,
        byc=byc,
        roster_size=int(raw.get("roster_size", 15)),
        roster_count=int(raw.get("roster_count", 15)),
        notes=tuple(raw.get("notes", []) or []),
    )


def list_scenarios(root: Path | str = SCENARIO_ROOT) -> list[Path]:
    return sorted(Path(root).glob("*.yaml"))


# --------------------------------------------------------------------------
# Staging a scenario against a snapshot
# --------------------------------------------------------------------------


def _roster(
    conn: sqlite3.Connection, snapshot_id: str, team: str, season: str, limit: int
) -> tuple[RosterEntry, ...]:
    """Top ``limit`` players by salary.

    By salary rather than at random because the tradeable assets are the ones
    that move the salary-matching arithmetic, and a context window spent on
    minimum contracts teaches the model nothing about the decision.
    """
    rows = conn.execute(
        "SELECT c.player_id, COALESCE(p.name, c.player_id) AS name, c.salary "
        "FROM contracts c LEFT JOIN players p "
        "  ON p.player_id = c.player_id AND p.snapshot_id = c.snapshot_id "
        "WHERE c.snapshot_id = ? AND c.team_id = ? AND c.season = ? "
        "ORDER BY c.salary DESC LIMIT ?",
        (snapshot_id, team, season, limit),
    ).fetchall()
    return tuple(
        RosterEntry(player_id=r["player_id"], name=r["name"], salary=int(r["salary"]))
        for r in rows
    )


def _team_salary(
    conn: sqlite3.Connection, snapshot_id: str, team: str, season: str
) -> int:
    (total,) = conn.execute(
        "SELECT COALESCE(SUM(salary), 0) FROM contracts "
        "WHERE snapshot_id = ? AND team_id = ? AND season = ?",
        (snapshot_id, team, season),
    ).fetchone()
    return int(total)


@dataclass
class StagedScenario:
    scenario: Scenario
    context: GMContext
    partner_salary: int
    snapshot_date: str
    #: Computed at staging time, before any model is asked anything. Both arms
    #: pay for it, so the blind arm can report the list its model never saw.
    scan: TargetScan = field(default_factory=TargetScan)

    @property
    def own_assets(self) -> dict[str, Asset]:
        return {
            e.player_id: Asset(e.player_id, e.name, e.salary)
            for e in self.context.own_roster
        }

    @property
    def partner_assets(self) -> dict[str, Asset]:
        return {
            e.player_id: Asset(e.player_id, e.name, e.salary)
            for e in self.context.partner_roster
        }

    @property
    def own_team(self) -> TeamTradeState:
        return TeamTradeState(
            team_id=self.context.team_id,
            team_salary=self.context.team_salary,
            roster_count=self.context.roster_count,
        )

    @property
    def partner_team(self) -> TeamTradeState:
        return TeamTradeState(
            team_id=self.context.partner_team,
            team_salary=self.partner_salary,
            roster_count=self.scenario.roster_count,
        )


def stage(scenario: Scenario) -> StagedScenario:
    """Load the snapshot and build the context the GM will see."""
    if not scenario.snapshot_dir.exists():
        raise ScenarioError(
            f"snapshot {scenario.snapshot!r} is not present. It is not "
            "redistributed — rebuild with `python -m mironba.data.ingest.build "
            f"--seasons {scenario.season}`."
        )
    conn = connect()
    try:
        initialize(conn)
        meta = load_snapshot(conn, scenario.snapshot_dir)
        own = _roster(
            conn, meta.snapshot_id, scenario.team, scenario.season, scenario.roster_size
        )
        partner = _roster(
            conn, meta.snapshot_id, scenario.partner, scenario.season, scenario.roster_size
        )
        if not own:
            raise ScenarioError(
                f"no {scenario.season} contracts for {scenario.team} in "
                f"{scenario.snapshot}"
            )
        if not partner:
            raise ScenarioError(
                f"no {scenario.season} contracts for {scenario.partner} in "
                f"{scenario.snapshot}"
            )
        team_salary = _team_salary(conn, meta.snapshot_id, scenario.team, scenario.season)
        partner_salary = _team_salary(
            conn, meta.snapshot_id, scenario.partner, scenario.season
        )
        as_of = meta.as_of_date
    finally:
        conn.close()

    env = environment_for(scenario.season)
    notes = list(scenario.notes)
    notes.append(
        "Payroll is a sum of season cap hits, not dated apron salary; it "
        "ignores dead money and cap holds."
    )
    if scenario.byc.is_assumption:
        notes.append(
            "Base-year compensation is ASSUMED resolved for this scenario, "
            "not sourced."
        )

    context = GMContext(
        team_id=scenario.team,
        season=scenario.season,
        scenario_seed=scenario.seed,
        own_roster=own,
        partner_team=scenario.partner,
        partner_roster=partner,
        team_salary=team_salary,
        tier=tier_for_salary(team_salary, env),
        roster_count=scenario.roster_count,
        trade_date=scenario.trade_date.isoformat(),
        notes=tuple(notes),
    )
    staged = StagedScenario(
        scenario=scenario,
        context=context,
        partner_salary=partner_salary,
        snapshot_date=as_of,
    )
    # Deterministic, model-free, and run in both arms. Attached to the context
    # rather than passed around so the "what may this agent know" question has
    # exactly one answer to read.
    staged.scan = scan_targets(
        own=staged.own_assets,
        theirs=staged.partner_assets,
        own_team=staged.own_team,
        partner_team=staged.partner_team,
        season=scenario.season,
        trade_date=scenario.trade_date,
        re_sign_status=scenario.byc.status,
        max_assets_out=scenario.persona.max_assets_out,
        env=env,
    )
    staged.context = replace(context, feasible_targets=tuple(staged.scan.targets))
    return staged
