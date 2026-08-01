"""One agent, one tick, one decision. The M1 end-to-end command.

    python -m mironba.sim.tick --scenario configs/scenario/curry-to-lakers.yaml

Prints the proposal, the verdict, the retry if there was one, and the manifest.
Writes everything under ``runs/<run_id>/``.

The control flow is the point, so it is written flat enough to read:

    choose action -> state intent -> SOLVE -> select by index -> judge
                                       |
                     unsatisfiable -> hand back the binding constraint
                                      -> revise intent -> SOLVE again

The solver is the only thing that builds a package, so every option the model
sees is legal before it sees it. The retry is worth something now: the old one
returned a rejection of a guess and asked for another guess, while this one
returns the shape of the constraint and asks for a different *want*.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from mironba.agents.gm import ARM_TEMPLATES, ARMS, TEMPLATES, GMAgent
from mironba.llm.client import (
    DEFAULT_CONFIG,
    SCHEMA_VERSION,
    LLMClient,
    SchemaFailure,
    load_config,
    preflight,
    probe_model,
    probe_runtime,
    resolve_profile,
)
from mironba.llm.canary import check_throughput, measure_throughput
from mironba.llm.probe import observed_enforcement
from mironba.llm.providers import ProviderError
from mironba.rules.trade_validator import Verdict, summarize
from mironba.rules.solver import TradeIntent, build_trade, constraint_explanation, solve
from mironba.sim.boundary import judge, rejection_reason
from mironba.sim.scenario import load_scenario, stage
from mironba.world.events import EventLog, EventType, Visibility
from mironba.world.manifest import Run, build_manifest, template_hash


class TickResult:
    def __init__(self) -> None:
        self.verdicts: list[Verdict] = []
        self.malformed: int = 0
        self.retried: bool = False
        self.stood_pat: bool = False
        self.schema_failed: bool = False
        #: Did the first intent have any legal package at all?
        self.first_intent_satisfiable: bool | None = None
        self.final_intent_satisfiable: bool | None = None
        self.packages_offered: int = 0
        self.declined_all: bool = False
        self.decline_reason: str = ""
        self.solver_seconds: list[float] = []
        self.binding_constraints: list[str] = []
        #: Which arm this trial ran in, and what the pre-filter found.
        self.arm: str = "blind"
        self.feasible_count: int = 0
        self.feasible_shown: bool = False
        self.unlocks_shown: bool = False
        #: Kept apart from solver_seconds on purpose. The pre-filter is one
        #: comparison per contract and the scan behind it is a full solve per
        #: survivor; blending them would hide which half grows at M3.
        self.prefilter_seconds: float = 0.0
        self.scan_solve_seconds: float = 0.0
        #: Did the model ask for someone the solver had already ruled out?
        self.targets_off_list: int = 0
        self.selection_index: int | None = None

    @property
    def final_verdict(self) -> Verdict | None:
        return self.verdicts[-1] if self.verdicts else None


def run_tick(
    scenario_path: Path | str,
    *,
    profile: str = "gm_agent",
    runs_dir: Path | str = "runs",
    config_path: Path | str | None = None,
    quiet: bool = False,
    seed: int | None = None,
    arm: str = "blind",
    strict_throughput: bool = False,
) -> tuple[TickResult, Run, LLMClient]:
    scenario = load_scenario(scenario_path)
    staged = stage(scenario)

    config = load_config(config_path or DEFAULT_CONFIG)
    cfg = resolve_profile(config, profile)
    if seed is not None:
        # A benchmark over N trials with one fixed seed is one trial repeated N
        # times, and would report a schema-failure rate of exactly 0% or 100%.
        # The override is per-trial and lands in that trial's manifest, so each
        # run stays individually reproducible while the set samples the
        # distribution.
        #
        # Patched into the config rather than the resolved profile because the
        # client resolves the profile again for every call; overriding only the
        # local copy would change the manifest and not the requests, which is
        # the worst of both — a manifest that lies.
        config = copy.deepcopy(config)
        config["profiles"][cfg.name]["seed"] = seed
        cfg = resolve_profile(config, profile)

    # Preflight before the manifest. A manifest naming a model the server
    # cannot serve is a false record of what produced the (absent) result.
    problems = preflight(cfg)
    if problems:
        raise ProviderError(
            "preflight failed:\n  " + "\n  ".join(problems)
            + f"\n\nProfile {cfg.name!r} wants {cfg.model!r} on {cfg.server}."
        )

    # Quantization from the server, not from the config file, so it cannot go
    # stale when someone pulls a different build of the same tag.
    info = probe_model(cfg)

    # Residency before the manifest, which means loading the model first: the
    # server reports nothing about an offload split until it has one.
    runtime = probe_runtime(cfg)

    # And throughput, because residency is not speed. gpu_fraction was 1.0
    # through a 3x slowdown: the weights were in VRAM and there was no room
    # left to run them in. Measured before the manifest so the manifest can
    # carry it, and checked against a baseline so a degraded machine stops
    # rather than quietly producing an incomparable latency column.
    # The canary exists to catch a LOCAL server that has silently spilled
    # weights to system RAM. Against a hosted model it measures network
    # latency and someone else's load, and a number that cannot fail is not a
    # check - recording one would let a run claim a passing canary it never had.
    hosted = cfg.server in ("anthropic", "claude_sdk")
    canary = None if hosted else measure_throughput(cfg)
    drift = None if hosted else check_throughput(cfg, canary)
    if drift and strict_throughput:
        raise ProviderError(drift)
    if drift:
        print(f"\nWARNING: {drift}\n", file=sys.stderr)

    # Manifest before the first token. A run that dies mid-call still knows
    # what it was.
    manifest = build_manifest(
        model_id=cfg.model,
        server=cfg.server,
        base_url=cfg.base_url,
        quantization=info.quantization,
        prompt_template_hash=template_hash(*TEMPLATES),
        snapshot_date=staged.snapshot_date,
        temperature=cfg.temperature,
        # Anthropic rejects temperature and top_p together, so the provider
        # sends temperature only; and the Messages API takes no seed at all.
        # Recording either here would put a value in the manifest that was
        # never sent, which is the same failure as recording a canary that
        # never ran. Both come back null, and sampling_not_settable says why.
        top_p=None if cfg.server == "anthropic" else cfg.top_p,
        seed=None if cfg.server in ("anthropic", "claude_sdk") else cfg.seed,
        thinking=cfg.thinking,
        schema_version=SCHEMA_VERSION,
        scenario_id=scenario.id,
        profile=cfg.name,
        byc_mode=scenario.byc.mode,
        byc_sourced=scenario.byc.sourced,
        persona=scenario.persona.to_dict(),
        snapshot=scenario.snapshot,
        server_context_length=info.context_length,
        model_size_bytes=runtime.size_bytes,
        model_size_vram_bytes=runtime.size_vram_bytes,
        gpu_fraction=runtime.gpu_fraction,
        fully_resident=runtime.fully_resident,
        runtime_context_length=runtime.context_length,
        canary_tokens_per_s=canary.tokens_per_s if canary else None,
        canary_wall_s=canary.wall_s if canary else None,
        canary_overhead_ratio=round(canary.overhead_ratio, 2) if canary else None,
        sampling_not_settable=(
            "top_p: Anthropic rejects it alongside temperature; seed: the "
            "Messages API has no seed parameter. Neither was sent, so both are "
            "null rather than the configured value. Runs are therefore NOT "
            "bit-reproducible on this provider - the manifest's reproducible "
            "flag reflects that."
            if cfg.server in ("anthropic", "claude_sdk") else None
        ),
        canary_not_applicable=(
            "hosted API: throughput measures network and provider load, not "
            "local weight residency. Recorded null rather than as a pass."
            if hosted else None
        ),
        # The experiment condition. Two runs identical in every other manifest
        # field can differ only here, so it has to be recorded here.
        arm=arm,
        # Hash of only the prompts this arm can reach, so the same arm stays
        # comparable across milestones that add prompts for other arms.
        arm_prompt_hash=template_hash(*ARM_TEMPLATES[arm]),
        feasible_targets=len(staged.scan.targets),
    )
    run = Run.start(manifest, runs_dir=runs_dir)
    # Measured once per process and cached: whether this server actually
    # constrains decoding, rather than whether we asked it to.
    client = LLMClient(
        run, config=config, schema_enforcement=observed_enforcement(cfg)
    )

    log = EventLog(run)
    log.emit(
        EventType.RUN_STARTED,
        scenario=scenario.id,
        team=scenario.team,
        partner=scenario.partner,
        model=cfg.model,
        profile=cfg.name,
    )

    agent = GMAgent(
        agent_id=scenario.team,
        persona=scenario.persona,
        client=client,
        log=log,
        profile=profile,
        arm=arm,
    )

    result = TickResult()
    result.arm = arm
    result.feasible_count = len(staged.scan.targets)
    result.feasible_shown = agent.shows_feasible(staged.context)
    result.unlocks_shown = agent.shows_unlocks(staged.context)
    result.prefilter_seconds = staged.scan.prefilter_s
    result.scan_solve_seconds = staged.scan.solve_s
    say = (lambda *a, **k: None) if quiet else print

    say(f"\n=== {scenario.id} — {scenario.team} GM, {scenario.season} ===")
    say(f"arm: {arm}   seed: {scenario.seed}\n")

    log.emit(
        EventType.TARGET_SCAN,
        actor="solver",
        arm=arm,
        shown_to_model=result.feasible_shown,
        unlocks_shown=result.unlocks_shown,
        feasible=list(staged.scan.ids),
        considered=staged.scan.considered,
        survived_bound=staged.scan.survived_bound,
        ceiling=staged.scan.ceiling,
        prefilter_s=round(staged.scan.prefilter_s, 5),
        solve_s=round(staged.scan.solve_s, 5),
        empty_reason=staged.scan.empty_reason,
    )
    say(f"FEASIBLE TARGETS: {staged.scan.explain()}")
    if staged.scan.any_feasible:
        say(staged.scan.render())
    else:
        # Not a failure. A team with nothing acquirable is a result about the
        # team, and saying so is the point of computing this before asking.
        say(f"  (none) {staged.scan.empty_reason}")
    say(f"  shown to model: {result.feasible_shown}\n")

    try:
        decision = agent.decide(staged.context)
    except SchemaFailure as exc:
        result.schema_failed = True
        log.emit(EventType.LLM_GAVE_UP, actor=scenario.team, error=str(exc))
        say(f"SCHEMA FAILURE: {exc}")
        _finish(run, log, client, result, quiet=quiet)
        return result, run, client

    if decision.action == "stand_pat":
        result.stood_pat = True
        say(f"ACTION: stand pat\n  reason: {decision.reason}")
        _finish(run, log, client, result, quiet=quiet)
        return result, run, client

    intent_form = decision.intent
    say("ACTION: pursue a trade")
    say(f"  reason: {decision.reason}")

    # One source for these, shared with the pre-filter, so the scan and the
    # solve can never disagree about what a contract is worth.
    own_assets = staged.own_assets
    partner_assets = staged.partner_assets
    own_team = staged.own_team
    partner_state = staged.partner_team
    feasible_ids = set(staged.scan.ids)

    solved = None
    for attempt in (1, 2):
        intent = TradeIntent(
            target_player_ids=tuple(intent_form.target_player_ids),
            tradeable_asset_ids=tuple(intent_form.tradeable_asset_ids),
            excluded_player_ids=tuple(intent_form.excluded_player_ids),
            priority=tuple(intent_form.priority),
            rationale=intent_form.reason,
        )
        # Asking for someone the scan already ruled out is the single most
        # informative thing the model can do here: in the feasible arm it means
        # it ignored the list, and in the blind arm it is the baseline rate the
        # list is meant to fix.
        off_list = [p for p in intent.target_player_ids if p not in feasible_ids]
        if attempt == 1:
            result.targets_off_list = len(off_list)

        say(f"\nINTENT (attempt {attempt}):")
        say(f"  want:     {', '.join(intent.target_player_ids) or '-'}")
        if off_list and staged.scan.any_feasible:
            say(f"  off-list: {', '.join(off_list)}  (not acquirable)")
        say(f"  offering: {', '.join(intent.tradeable_asset_ids) or '-'}")
        if intent.excluded_player_ids:
            say(f"  excluded: {', '.join(intent.excluded_player_ids)}")
        say(f"  reason:   {intent_form.reason}")

        solved = solve(
            intent,
            own=own_assets,
            theirs=partner_assets,
            own_team=own_team,
            partner_team=partner_state,
            season=scenario.season,
            trade_date=scenario.trade_date,
            re_sign_status=scenario.byc.status,
            max_assets_out=scenario.persona.max_assets_out,
        )
        result.solver_seconds.append(solved.elapsed_s)
        if attempt == 1:
            result.first_intent_satisfiable = solved.satisfiable
        result.final_intent_satisfiable = solved.satisfiable

        log.emit(
            EventType.SOLVER_RESULT,
            actor="solver",
            attempt=attempt,
            satisfiable=solved.satisfiable,
            packages=len(solved.packages),
            feasible_found=solved.feasible_found,
            examined=solved.examined,
            elapsed_s=round(solved.elapsed_s, 4),
            binding_constraint=solved.binding_constraint,
        )
        say(f"  solver:   {solved.explain()}")

        if solved.satisfiable or attempt == 2:
            break

        result.binding_constraints.append(solved.binding_constraint or "UNKNOWN")
        explanation = constraint_explanation(solved)
        say(f"\n  {explanation.splitlines()[0]}")
        result.retried = True
        try:
            intent_form = agent.revise_intent(staged.context, intent_form, explanation)
        except SchemaFailure as exc:
            result.schema_failed = True
            say(f"SCHEMA FAILURE on revised intent: {exc}")
            _finish(run, log, client, result, quiet=quiet)
            return result, run, client

    if not solved.satisfiable:
        result.binding_constraints.append(solved.binding_constraint or "UNKNOWN")
        say(f"\nNO LEGAL PACKAGE. Binding: {solved.binding_constraint}")
        _finish(run, log, client, result, quiet=quiet)
        return result, run, client

    result.packages_offered = len(solved.packages)
    options = "\n".join(
        f"  [{i}] {pkg.describe(own_assets, partner_assets)}"
        for i, pkg in enumerate(solved.packages)
    )
    say("\nLEGAL PACKAGES:")
    say(options)

    try:
        choice = agent.select_package(staged.context, options, len(solved.packages))
    except SchemaFailure as exc:
        result.schema_failed = True
        say(f"SCHEMA FAILURE on selection: {exc}")
        _finish(run, log, client, result, quiet=quiet)
        return result, run, client

    if choice.declined:
        result.declined_all = True
        result.decline_reason = choice.reason
        say(f"\nDECLINED ALL {len(solved.packages)} legal package(s)")
        say(f"  reason: {choice.reason}")
        _finish(run, log, client, result, quiet=quiet)
        return result, run, client

    result.selection_index = choice.selection
    package = solved.packages[choice.selection]
    say(f"\nSELECTED [{choice.selection}]: {package.describe(own_assets, partner_assets)}")
    say(f"  reason: {choice.reason}")

    trade = build_trade(
        package.send_player_ids,
        package.receive_player_ids,
        own=own_assets,
        theirs=partner_assets,
        own_team=own_team,
        partner_team=partner_state,
        season=scenario.season,
        trade_date=scenario.trade_date,
        re_sign_status=scenario.byc.status,
    )
    log.emit(
        EventType.PROPOSAL_ASSEMBLED,
        actor=scenario.team,
        players=[
            {"id": p.player_id, "salary": p.salary, "from": p.from_team}
            for p in trade.players
        ],
    )

    # Judged again even though the solver already validated it. Cheap, and it
    # means the event log records a verdict produced by the same call any
    # reader would make, rather than one inherited from a search.
    validation = judge(trade)
    result.verdicts.append(validation.verdict)
    log.emit(
        EventType.VERDICT,
        actor="rules",
        visibility=Visibility.PUBLIC,
        verdict=validation.verdict.value,
        findings=[str(f) for f in validation.findings],
    )
    say(f"\nVERDICT: {validation.verdict.value.upper()}")
    say(summarize(validation))

    _finish(run, log, client, result, quiet=quiet)
    return result, run, client


def _print_proposal(say, proposal, staged) -> None:
    own = {p.player_id: p for p in staged.context.own_roster}
    theirs = {p.player_id: p for p in staged.context.partner_roster}
    say(f"  partner: {proposal.partner_team}")
    for pid in proposal.send_player_ids:
        entry = own.get(pid)
        label = f"{entry.name} (${entry.salary:,})" if entry else "UNKNOWN ID"
        say(f"    out  {pid:<12} {label}")
    for pid in proposal.receive_player_ids:
        entry = theirs.get(pid)
        label = f"{entry.name} (${entry.salary:,})" if entry else "UNKNOWN ID"
        say(f"    in   {pid:<12} {label}")
    say(f"  rationale: {proposal.reason}")


def _finish(
    run: Run,
    log: EventLog,
    client: LLMClient,
    result: TickResult,
    *,
    quiet: bool = False,
) -> None:
    """Close the run and report. Called on every exit path, including failures.

    The manifest prints here rather than at the end of ``run_tick`` because
    ``run_tick`` has several early returns — standing pat, a schema failure —
    and a manifest that only prints on the happy path is missing exactly when
    you most want to know what produced the result.
    """
    log.emit(
        EventType.RUN_FINISHED,
        verdict=result.final_verdict.value if result.final_verdict else None,
        malformed=result.malformed,
        retried=result.retried,
        stood_pat=result.stood_pat,
        schema_failed=result.schema_failed,
        first_intent_satisfiable=result.first_intent_satisfiable,
        final_intent_satisfiable=result.final_intent_satisfiable,
        packages_offered=result.packages_offered,
        declined_all=result.declined_all,
        decline_reason=result.decline_reason,
        solver_seconds=result.solver_seconds,
        binding_constraints=result.binding_constraints,
        arm=result.arm,
        feasible_count=result.feasible_count,
        feasible_shown=result.feasible_shown,
        unlocks_shown=result.unlocks_shown,
        prefilter_seconds=result.prefilter_seconds,
        scan_solve_seconds=result.scan_solve_seconds,
        targets_off_list=result.targets_off_list,
        selection_index=result.selection_index,
    )
    run.write_json("stats.json", client.stats.to_dict())
    if quiet:
        return
    print("\n=== MANIFEST ===")
    print(run.manifest.summary())
    print(f"\n=== STATS ===\n  {client.stats.to_dict()}")
    print(f"\nartifacts: {run.dir}")


def use_utf8_console() -> None:
    """Stop cp1252 from killing a run over a player's name.

    Windows consoles default to cp1252, and an NBA roster is wall-to-wall
    Jokic, Doncic and Vucevic with the diacritics intact. Printing one raised
    UnicodeEncodeError *after* the model call and the verdict, throwing away a
    completed run at the display step. Errors are replaced rather than raised:
    a mangled character in a console line is not worth losing a result over,
    and the artifacts on disk are UTF-8 regardless.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="Run one M1 tick end to end.")
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--profile", default="gm_agent")
    parser.add_argument("--runs-dir", default="runs", type=Path)
    parser.add_argument("--config", default=None, type=Path)
    parser.add_argument("--seed", type=int, default=None,
                        help="override the profile seed for this run")
    parser.add_argument("--arm", default="blind", choices=list(ARMS),
                        help="blind = M1.5 behaviour; feasible = show the "
                             "solver-computed acquirable targets")
    args = parser.parse_args(argv)

    try:
        run_tick(
            args.scenario,
            profile=args.profile,
            runs_dir=args.runs_dir,
            config_path=args.config,
            seed=args.seed,
            arm=args.arm,
        )
    except ProviderError as exc:
        print(f"\nserver problem: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
