"""Run a STIPULATED scenario: an asserted event, then the league's reaction.

    python -m mironba.sim.stipulated --scenario curry-lakers-2026

The pending-decision path forks the world on an unresolved question and can
score the branch that happened. This path is the project's founding example -
"Stephen Curry traded to the Lakers" - and it is a different animal:

* **The event is stipulated.** No branches, no pending decision. The scenario
  file asserts WHO moves WHERE, and the question is only what follows.
* **The trade must pass rules/ first.** The scenario declares players and
  directions; salaries and payrolls are derived from the contract snapshot and
  the package goes through ``rules/trade_validator.py`` before anything else
  runs. If the validator refuses it, this runner prints the findings and
  exits - it never bypasses the rules to make the premise happen.
* **The output is UNFALSIFIABLE and says so.** There is no world where this
  trade occurred, so there is no ground truth, no score, and no null. What
  the charter's discipline still buys here is provenance: dated inputs, a
  deterministic seed, and a manifest - the run is reproducible even though it
  is not checkable. It is a demonstration, not a measurement.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    PlayerAsset,
    TeamTradeState,
    Trade,
    validate_trade,
)

UNFALSIFIABLE = (
    "UNFALSIFIABLE: the seed event is stipulated, so no ground truth exists "
    "or ever will. Nothing below is scored and no null is stated because "
    "there is nothing to compare against. This output is a demonstration of "
    "the machinery, not a measurement."
)


def build_trade(scenario, league) -> Trade:
    """The declared package, with every dollar derived from the snapshot."""
    spec = scenario.stipulation
    salaries = {r["player_id"]: int(r["salary"]) for r in league.contracts_2627}
    payroll: dict[str, int] = {}
    roster: dict[str, set] = {}
    for r in league.contracts_2627:
        payroll[r["team_id"]] = payroll.get(r["team_id"], 0) + int(r["salary"])
        roster.setdefault(r["team_id"], set()).add(r["player_id"])

    players = []
    teams = set()
    for move in spec["players"]:
        pid = move["player_id"]
        if pid not in salaries:
            raise SystemExit(
                f"{pid} has no {scenario.next_season} contract row; a "
                "stipulated trade cannot invent a salary"
            )
        players.append(PlayerAsset(
            player_id=pid, name=league.name(pid), salary=salaries[pid],
            from_team=move["from"], to_team=move["to"],
        ))
        teams.update((move["from"], move["to"]))

    return Trade(
        season=scenario.next_season,
        trade_date=scenario.freeze,
        teams=tuple(
            TeamTradeState(t, payroll.get(t, 0), len(roster.get(t, ())))
            for t in sorted(teams)
        ),
        players=tuple(players),
        label=spec.get("label", scenario.id),
    )


def apply_trade(league, trade: Trade) -> None:
    """Move the traded contracts between teams in the loaded world state."""
    dest = {p.player_id: p.to_team for p in trade.players}
    for row in league.contracts_2627:
        if row["player_id"] in dest:
            row["team_id"] = dest[row["player_id"]]


def react(scenario, league_mod, seed: int, seed_trade=None):
    """One full reaction - market signings, then the trade cascade.

    ``seed_trade=None`` is the NULL: the same world, same date, same seed,
    with nothing stipulated. Only trades that appear in the seeded run and
    not here are attributable to the seed.
    """
    from mironba.sim.cascade import run_cascade

    league = league_mod.LeagueState.load()
    movers = frozenset()
    if seed_trade is not None:
        apply_trade(league, seed_trade)
        movers = frozenset(p.player_id for p in seed_trade.players)
    results, contests, scheduler = league_mod.run_branch(
        "stipulated", league, [], seed=seed, stipulated=movers
    )
    for team in league_mod.TEAMS:
        leaked = movers & set(results[team].signed)
        assert not leaked, (
            f"stipulation violated: {team} signed {sorted(leaked)} - a "
            "stipulated player changed teams during the reaction"
        )
    cascade = run_cascade(
        league, results, season=scenario.season, when=scenario.freeze,
        trade_season=scenario.next_season, teams=league_mod.TEAMS,
        persona_for=league_mod.persona_for, scheduler=scheduler,
        stipulated=movers,
    )
    for trade in cascade.trades:
        touched = movers & (set(trade.received) | set(trade.sent))
        assert not touched, (
            f"stipulation violated: generated trade moved {sorted(touched)}"
        )
    return league, results, contests, scheduler, cascade


def main(argv=None) -> int:
    from mironba.sim.tick import use_utf8_console
    from mironba.world.scenario import load_scenario

    use_utf8_console()
    parser = argparse.ArgumentParser(
        description="Run a stipulated scenario: validate the asserted event, "
                    "then simulate the league's reaction.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    scenario = load_scenario(args.scenario)
    if scenario.kind != "stipulated":
        raise SystemExit(
            f"{scenario.id} is a {scenario.kind} scenario; use "
            "mironba.sim.league / mironba.eval.backtest for those"
        )

    import mironba.sim.league as league_mod

    league_mod.bind_scenario(scenario)
    league_mod.TEAMS = league_mod._all_teams()
    league = league_mod.LeagueState.load()
    env = environment_for(scenario.next_season)

    print("=" * 78)
    print(f"  MiroNBA - {scenario.id} (STIPULATED)")
    print("=" * 78)
    print(f"  {UNFALSIFIABLE}")
    print(f"\n  freeze  {scenario.freeze} ({scenario.freeze_rationale.strip()})")

    # -- 1. The stipulated event goes through rules/ before anything else ----
    trade = build_trade(scenario, league)
    verdict = validate_trade(trade, env)
    print(f"\n  STIPULATED EVENT: {trade.label}")
    for p in trade.players:
        print(f"    {league.name(p.player_id):<22} {p.from_team} -> {p.to_team}"
              f"   ${p.salary:,}")
    findings = list(getattr(verdict, "findings", []) or [])
    for f in findings:
        print(f"    {f}")
    if not verdict.legal:
        print("\n  REFUSED BY rules/trade_validator.py - the stipulation is not")
        print("  a legal trade, and this runner does not bypass the rules to")
        print("  make a premise happen. Fix the declared package or accept")
        print("  that the counterfactual is not constructible.")
        return 1
    print("    validator verdict: LEGAL - the reaction may proceed")

    # -- 2. Apply it, then thirty teams react ---------------------------------
    before = league_mod.project_wins(league, {
        t: league.freeze_state(t, set()) for t in league_mod.TEAMS
    })
    apply_trade(league, trade)
    after = league_mod.project_wins(league, {
        t: league.freeze_state(t, set()) for t in league_mod.TEAMS
    })
    if before and after:
        moved = sorted(
            ((t, after[t] - before[t]) for t in after if t in before),
            key=lambda x: -abs(x[1]),
        )
        print("\n  CONTENTION SHIFT (projected wins, value model; deterministic)")
        shown = [(t, d) for t, d in moved[:6] if abs(d) > 0.05]
        for t, d in shown:
            print(f"    {t}  {d:+.1f}")
        if not shown:
            print("    no team moved by more than 0.05 projected wins - the "
                  "value model's\n    name-matching covers ~3/4 of a roster "
                  "and unmatched players fall to\n    replacement level, "
                  "which can flatten a single-star swap to nothing.\n    "
                  "Reported as produced, not patched.")

    seeded_league, results, contests, scheduler, cascade = react(
        scenario, league_mod, args.seed, seed_trade=trade
    )
    league = seeded_league
    print("\n  REACTION - every team plans its offseason in the stipulated world")
    for team in league_mod.TEAMS:
        r = results[team]
        signed = ", ".join(league.name(p) for p in r.signed)
        if signed or r.lost_contests:
            lost = ", ".join(league.name(p) for p in r.lost_contests)
            line = f"    {team:<4} signs: {signed or '-'}"
            if lost:
                line += f"   loses contest: {lost}"
            print(line)
    contested = [c for c in contests if c.contested]
    arbitrary = sum(1 for c in contested if "arbitrary" in c.reason)
    print(f"\n  {len(contested)} contested players, {arbitrary} resolved arbitrarily")
    print(f"  scheduler: {scheduler.wakes} wakes vs {scheduler.polled_equivalent} "
          f"polled ({scheduler.saving:.0%} saved)")

    # -- 3. The trade cascade, and its null -------------------------------
    from mironba.sim.cascade import MAX_DEPTH

    print("\n  TRADE CASCADE (deterministic intent; solver and rules unchanged)")
    for t in cascade.trades:
        print(t.line(name=league.name))
    if not cascade.trades:
        print("    no generated trades")
    print(f"    attempts {cascade.attempts}; killed by counterparty gate "
          f"{cascade.killed_by_gate}, by solver {cascade.killed_by_solver}; "
          f"suppressed by cap {cascade.suppressed_by_cap}")
    print(f"    depth reached {cascade.depth_reached} of {MAX_DEPTH} "
          f"(depth cap {'bound' if cascade.depth_reached >= MAX_DEPTH else 'did not bind'}; "
          f"one-attempt-per-team cap suppressed {cascade.suppressed_by_cap} "
          "repeat wakes)")

    print("\n  THE NULL - the same world, same seed, WITHOUT the stipulated trade")
    _, _, _, _, null_cascade = react(scenario, league_mod, args.seed,
                                     seed_trade=None)
    seeded_keys = {t.key(): t for t in cascade.trades}
    null_keys = {t.key(): t for t in null_cascade.trades}
    attributable = [t for k, t in seeded_keys.items() if k not in null_keys]
    vanished = [t for k, t in null_keys.items() if k not in seeded_keys]
    print(f"    unseeded run generated {len(null_cascade.trades)} trade(s); "
          f"seeded run {len(cascade.trades)}")
    print(f"    ATTRIBUTABLE TO THE SEED: {len(attributable)} trade(s) - the "
          "headline; a cascade that")
    print("    would have happened anyway is not a cascade.")
    for t in attributable:
        print("  " + t.line(name=league.name))
    if vanished:
        print(f"    displaced by the seed ({len(vanished)} trade(s) that happen "
              "only WITHOUT it):")
        for t in vanished:
            print("  " + t.line(name=league.name))

    print(f"\n  NOT SCORED. {UNFALSIFIABLE}")

    if args.out:
        manifest = {
            "scenario": scenario.id,
            "kind": scenario.kind,
            "unfalsifiable": True,
            "label": UNFALSIFIABLE,
            "seed": args.seed,
            "model": None,
            "model_reason": "deterministic run; no LLM call is made",
            "temperature": None,
            "temperature_reason": "no sampling occurs",
            "prompt_template_hash": None,
            "prompt_template_hash_reason": "no prompt is rendered",
            "data_snapshot": scenario.next_season,
            "freeze": scenario.freeze.isoformat(),
            "trade": {
                "label": trade.label,
                "legal": verdict.legal,
                "players": [
                    {"player_id": p.player_id, "from": p.from_team,
                     "to": p.to_team, "salary": p.salary}
                    for p in trade.players
                ],
                "findings": [str(f) for f in findings],
            },
            "reaction": {
                t: asdict(results[t]) for t in league_mod.TEAMS
            },
            "cascade": {
                "label": UNFALSIFIABLE,
                "seeded_trades": [asdict(t) for t in cascade.trades],
                "unseeded_trades": [asdict(t) for t in null_cascade.trades],
                "attributable_to_seed": [asdict(t) for t in attributable],
                "displaced_by_seed": [asdict(t) for t in vanished],
                "killed_by_counterparty_gate": cascade.killed_by_gate,
                "killed_by_solver": cascade.killed_by_solver,
                "depth_reached": cascade.depth_reached,
                "cap_bound": cascade.cap_bound,
            },
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(manifest, indent=2, default=str),
                            encoding="utf-8")
        print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
