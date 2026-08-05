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
from datetime import datetime, timezone
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


def react(scenario, league_mod, seed: int, seed_trade=None, revealed=None,
          obligations=None, seed_signing=None, signing_salary=0):
    """One full reaction - market signings, then the trade cascade.

    ``seed_trade=None`` and ``seed_signing=None`` together are the NULL: the
    same world, same date, same seed, with nothing stipulated. Only what
    appears in the seeded run and not there is attributable to the seed.

    A signing seed is applied by ADDING a contract row rather than moving
    one, and its signee joins ``movers`` for exactly the same reason a
    traded player does: a stipulated player may not change teams during the
    reaction, whichever way he arrived.
    """
    from mironba.sim.cascade import run_cascade

    league = league_mod.LeagueState.load()
    movers = frozenset()
    if seed_trade is not None:
        apply_trade(league, seed_trade)
        movers = frozenset(p.player_id for p in seed_trade.players)
    if seed_signing is not None:
        from mironba.sim.signing_seed import apply_signing

        apply_signing(league, seed_signing, signing_salary)
        movers = movers | {seed_signing.player_id}
    results, contests, scheduler = league_mod.run_branch(
        "stipulated", league, [], seed=seed, stipulated=movers,
        revealed=revealed, obligations=obligations,
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
        stipulated=movers, revealed=revealed,
    )
    for trade in cascade.trades:
        touched = movers & (set(trade.received) | set(trade.sent))
        assert not touched, (
            f"stipulation violated: generated trade moved {sorted(touched)}"
        )
    # A stipulated SIGNEE must end on the team he was stipulated to. He was
    # not on any roster before the seed, so an absent row is as much a
    # violation as a wrong one.
    if seed_signing is not None:
        where = {r["player_id"]: r["team_id"] for r in league.contracts_2627}
        assert where.get(seed_signing.player_id) == seed_signing.to_team, (
            f"stipulation violated: {seed_signing.player_id} ended on "
            f"{where.get(seed_signing.player_id)}, stipulated "
            f"{seed_signing.to_team}"
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
    # Two kinds of seed. A trade is judged by the trade validator; a signing
    # has no counterparty and no salary matching, so the trade validator has
    # nothing to say about it and the question is instead whether the
    # destination has a legal ROUTE. Same boundary either way: the sentence
    # names people and teams, rules/ decides whether that is possible.
    is_signing = bool((scenario.stipulation or {}).get("signing"))
    trade = None
    signing = None
    signing_routes_result = None
    signing_route = None
    route_source = ""
    signing_salary = 0
    findings = []
    verdict = None

    if is_signing:
        from mironba.sim.signing_seed import (build_signing, chosen_route,
                                              routes_for)

        signing = build_signing(scenario, league, league_mod)
        signing_routes_result = routes_for(signing, league, env)
        print(f"\n  STIPULATED EVENT: {signing.label}")
        print(f"    {signing.name:<22} free agent -> {signing.to_team}")
        if signing_routes_result.routes:
            print(f"    {len(signing_routes_result.routes)} legal route(s) "
                  f"for {signing.to_team} on {scenario.freeze}:")
            for route in signing_routes_result.routes:
                print(f"      {route.describe()}")
        signing_route, route_source = chosen_route(signing,
                                                   signing_routes_result)
        if signing_route is None:
            print("\n  REFUSED BY rules/signing.py - there is no legal route")
            print("  for this team to sign this player on the seed date, and")
            print("  this runner does not bypass the rules to make a premise")
            print("  happen. The binding constraints, quoted:")
            for name, why in sorted(signing_routes_result.blocked.items()):
                print(f"    {name}: {why}")
            if route_source:
                print(f"    {route_source}")
            return 1
        signing_salary = signing_route.max_first_year
        print(f"    route used: {signing_route.route} at "
              f"${signing_salary:,} - {route_source}")
        print("    signing solver verdict: LEGAL - the reaction may proceed")
    else:
        trade = build_trade(scenario, league)
        verdict = validate_trade(trade, env)
        print(f"\n  STIPULATED EVENT: {trade.label}")
        for p in trade.players:
            print(f"    {league.name(p.player_id):<22} {p.from_team} -> "
                  f"{p.to_team}   ${p.salary:,}")
        findings = list(getattr(verdict, "findings", []) or [])
        for f in findings:
            print(f"    {f}")
        if not verdict.legal:
            print("\n  REFUSED BY rules/trade_validator.py - the stipulation "
                  "is not")
            print("  a legal trade, and this runner does not bypass the rules "
                  "to")
            print("  make a premise happen. Fix the declared package or accept")
            print("  that the counterfactual is not constructible.")
            return 1
        print("    validator verdict: LEGAL - the reaction may proceed")

    # -- 2. Apply it, then thirty teams react ---------------------------------
    before = league_mod.project_wins(league, {
        t: league.freeze_state(t, set()) for t in league_mod.TEAMS
    })
    if is_signing:
        from mironba.sim.signing_seed import apply_signing

        apply_signing(league, signing, signing_salary)
    else:
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

    from mironba.sim.obligations import obligations_from

    duties = obligations_from(findings, env)
    if duties:
        print("\n  OBLIGATIONS THE SEED CREATED (from rules/, not chosen)")
        for team, line in sorted(duties.hard_caps.items()):
            print(f"    {team}  HARD_CAP: may not exceed ${line:,} for the "
                  "rest of the run")
        for team, short in sorted(duties.roster_shortfall.items()):
            print(f"    {team}  ROSTER_MINIMUM: must add {short} player(s) to "
                  "reach the minimum")
    seeded_league, results, contests, scheduler, cascade = react(
        scenario, league_mod, args.seed, seed_trade=trade, obligations=duties,
        seed_signing=signing, signing_salary=signing_salary,
    )
    league = seeded_league
    forced = [t for t in league_mod.TEAMS
              if results[t].obligations or results[t].unmet]
    if duties:
        print(f"\n  OBLIGATIONS DISCHARGED - {len(forced)} team(s) acted "
              "because the rules required it")
        for team in forced:
            for row in results[team].obligations:
                print(f"    {team}  {row['rule']}: signed "
                      f"{league.name(row['player_id'])} via {row['route']} "
                      f"at ${row['salary']:,}")
            for row in results[team].unmet:
                print(f"    {team}  {row['rule']} UNMET, short by "
                      f"{row['short_by']}: {row['reason']}")
        for team, line in sorted(duties.hard_caps.items()):
            end = results[team].committed_end
            print(f"    {team}  ends at ${end:,} against its ${line:,} hard "
                  f"cap - {'WITHIN' if end <= line else 'OVER'}")

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
    _, null_results, null_contests, _, null_cascade = react(
        scenario, league_mod, args.seed, seed_trade=None)
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

    # The cascade is not the only thing the seed moves. The same null run
    # already computed every team's signings and every contested player's
    # winner WITHOUT the seed; diffing them costs nothing and turns "here is
    # what happened" into "here is what the seed CAUSED to happen".
    signing_changes = []
    for team in league_mod.TEAMS:
        with_seed = set(results[team].signed)
        without = set(null_results[team].signed)
        if with_seed != without:
            signing_changes.append({
                "team": team,
                "only_with_seed": sorted(with_seed - without),
                "only_without_seed": sorted(without - with_seed),
            })
    null_winner = {c.player_id: c.winner for c in null_contests}
    contest_changes = [
        {"player_id": c.player_id, "with_seed": c.winner,
         "without_seed": null_winner[c.player_id], "reason": c.reason}
        for c in contests
        if c.player_id in null_winner and null_winner[c.player_id] != c.winner
    ]
    # WHO ELSE WANTED HIM. The seeded run cannot answer this: a stipulated
    # player is excluded from the pool precisely so he cannot sign anywhere
    # else, so no contest for him exists there. The UNSEEDED run has one,
    # and its offers are teams that made a legal offer under an enumerated
    # route - not teams a model guessed were interested.
    pursuit = []
    if is_signing:
        from mironba.sim.signing_seed import pursuers_of

        offers = pursuers_of(signing.player_id, null_contests)
        null_winner_of = {c.player_id: c.winner for c in null_contests}
        for offer in offers:
            team = offer["team"]
            with_seed = set(results[team].signed)
            without = set(null_results[team].signed)
            pursuit.append({
                "team": team,
                "route": offer["route"],
                "amount": offer["amount"],
                "won_him_without_the_seed":
                    null_winner_of.get(signing.player_id) == team,
                "did_instead": sorted(with_seed - without),
                "missed_out_on": sorted(without - with_seed),
            })
        print(f"\n  WHO ELSE WANTED {signing.name.upper()}")
        if not pursuit:
            print("    nobody. In the run without the seed no other team made "
                  "a legal offer for him,\n    so the stipulation displaced "
                  "no competing interest - a real answer, not a gap.")
        for row in pursuit:
            won = " (won him without the seed)" if row[
                "won_him_without_the_seed"] else ""
            did = ", ".join(league.name(p) for p in row["did_instead"]) or "-"
            missed = ", ".join(league.name(p)
                               for p in row["missed_out_on"]) or "-"
            print(f"    {row['team']:<4} offered {row['route']} up to "
                  f"${row['amount']:,}{won}")
            print(f"         instead signed: {did}   lost out on: {missed}")

    print(f"\n  REACTION DIFF vs the same run without the seed")
    print(f"    {len(signing_changes)} team(s) signed differently; "
          f"{len(contest_changes)} contested player(s) went elsewhere")
    for row in signing_changes:
        gained = ", ".join(league.name(p) for p in row["only_with_seed"]) or "-"
        lost = ", ".join(league.name(p) for p in row["only_without_seed"]) or "-"
        print(f"    {row['team']:<4} with seed: {gained}   without: {lost}")
    for row in contest_changes:
        print(f"    {league.name(row['player_id']):<22} "
              f"{row['without_seed']} -> {row['with_seed']} [{row['reason']}]")

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
            "seed_kind": "signing" if is_signing else "trade",
            "trade": None if is_signing else {
                "label": trade.label,
                "legal": verdict.legal,
                "players": [
                    {"player_id": p.player_id, "from": p.from_team,
                     "to": p.to_team, "salary": p.salary}
                    for p in trade.players
                ],
                "findings": [str(f) for f in findings],
            },
            "signing": None if not is_signing else {
                "label": signing.label,
                "legal": True,
                "player_id": signing.player_id,
                "name": signing.name,
                "to": signing.to_team,
                "route": signing_route.route,
                "salary": signing_salary,
                "salary_source": route_source,
                "routes": [
                    {"route": r.route, "max_first_year": r.max_first_year,
                     "max_years": r.max_years, "raise_pct": r.raise_pct,
                     "hard_cap": r.hard_cap, "describe": r.describe()}
                    for r in signing_routes_result.routes
                ],
                "blocked": dict(signing_routes_result.blocked),
            },
            "pursuit": pursuit,
            "reaction": {
                t: asdict(results[t]) for t in league_mod.TEAMS
            },
            # Every contested player, with WHO bid and WHY it resolved as
            # it did. The reason is the resolver's own string, ARBITRARY
            # included - a contested player decided by a coin flip and one
            # decided by a higher offer are different claims, and a report
            # that flattens them is asserting signal that is not there.
            "contests": [
                {"player_id": c.player_id, "winner": c.winner,
                 "reason": c.reason, "contested": c.contested,
                 "offers": [{"team": o.team, "route": o.route,
                             "amount": o.max_first_year}
                            for o in sorted(c.offers,
                                            key=lambda o: -o.max_first_year)]}
                for c in contests
            ],
            "obligations": {
                "hard_caps": duties.hard_caps,
                "roster_shortfall": duties.roster_shortfall,
                "findings_seen": duties.seen,
                "teams_forced": duties.teams_forced,
                "discharged": [
                    {"team": t, "signed": results[t].obligations,
                     "unmet": results[t].unmet}
                    for t in league_mod.TEAMS
                    if results[t].obligations or results[t].unmet
                ],
                "hard_cap_respected": {
                    t: results[t].committed_end <= line
                    for t, line in duties.hard_caps.items()
                },
            },
            "cascade": {
                "label": UNFALSIFIABLE,
                "seeded_trades": [asdict(t) for t in cascade.trades],
                "unseeded_trades": [asdict(t) for t in null_cascade.trades],
                "attributable_to_seed": [asdict(t) for t in attributable],
                "displaced_by_seed": [asdict(t) for t in vanished],
                "signings_changed": signing_changes,
                "contests_changed": contest_changes,
                "killed_by_counterparty_gate": cascade.killed_by_gate,
                "killed_by_solver": cascade.killed_by_solver,
                "depth_reached": cascade.depth_reached,
                "cap_bound": cascade.cap_bound,
            },
        }
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(manifest, indent=2, default=str),
                            encoding="utf-8")
        print(f"\n  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
