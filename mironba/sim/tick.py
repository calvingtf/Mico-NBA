"""One agent, one tick, one decision. The M1 end-to-end command.

    python -m mironba.sim.tick --scenario configs/scenario/curry-to-lakers.yaml

Prints the proposal, the verdict, the retry if there was one, and the manifest.
Writes everything under ``runs/<run_id>/``.

The control flow is the point of M1, so it is written flat enough to read:

    choose action -> propose -> assemble -> judge
                                              |
                        rejected -> hand the reason back -> revise -> judge again

Exactly one retry. A second rejection stands. Looping until the model stumbles
into something legal would turn the validator into a search oracle and make the
illegal-proposal rate meaningless — the number would measure how many attempts
we allowed, not how well the model understands the CBA.
"""

from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from mironba.agents.gm import TEMPLATES, GMAgent
from mironba.llm.client import (
    DEFAULT_CONFIG,
    SCHEMA_VERSION,
    LLMClient,
    SchemaFailure,
    load_config,
    preflight,
    probe_model,
    resolve_profile,
)
from mironba.llm.providers import ProviderError
from mironba.rules.trade_validator import Verdict, summarize
from mironba.sim.boundary import MalformedProposal, assemble, judge, rejection_reason
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
        top_p=cfg.top_p,
        seed=cfg.seed,
        thinking=cfg.thinking,
        schema_version=SCHEMA_VERSION,
        scenario_id=scenario.id,
        profile=cfg.name,
        byc_mode=scenario.byc.mode,
        byc_sourced=scenario.byc.sourced,
        persona=scenario.persona.to_dict(),
        snapshot=scenario.snapshot,
        server_context_length=info.context_length,
    )
    run = Run.start(manifest, runs_dir=runs_dir)
    client = LLMClient(run, config=config)

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
    )

    result = TickResult()
    say = (lambda *a, **k: None) if quiet else print

    say(f"\n=== {scenario.id} — {scenario.team} GM, {scenario.season} ===")
    say(f"seed: {scenario.seed}\n")

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

    proposal = decision.proposal
    say("ACTION: propose trade")
    say(f"  reason: {decision.reason}")
    _print_proposal(say, proposal, staged)

    for attempt in (1, 2):
        try:
            trade = assemble(
                proposal,
                staged.context,
                scenario.persona,
                trade_date=scenario.trade_date,
                partner_salary=staged.partner_salary,
                partner_roster_count=scenario.roster_count,
                byc=scenario.byc,
            )
        except MalformedProposal as exc:
            result.malformed += 1
            log.emit(
                EventType.PROPOSAL_MALFORMED,
                actor=scenario.team,
                attempt=attempt,
                reasons=exc.reasons,
            )
            say(f"\nMALFORMED (attempt {attempt}):")
            for reason in exc.reasons:
                say(f"  - {reason}")
            if attempt == 2:
                break
            result.retried = True
            try:
                proposal = agent.revise(
                    staged.context, proposal, "\n".join(f"- {r}" for r in exc.reasons)
                )
            except SchemaFailure as exc2:
                result.schema_failed = True
                say(f"SCHEMA FAILURE on retry: {exc2}")
                break
            say("\nREVISED PROPOSAL:")
            _print_proposal(say, proposal, staged)
            continue

        log.emit(
            EventType.PROPOSAL_ASSEMBLED,
            actor=scenario.team,
            attempt=attempt,
            players=[
                {"id": p.player_id, "salary": p.salary, "from": p.from_team}
                for p in trade.players
            ],
        )

        validation = judge(trade)
        result.verdicts.append(validation.verdict)
        log.emit(
            EventType.VERDICT,
            actor="rules",
            visibility=Visibility.PUBLIC,
            attempt=attempt,
            verdict=validation.verdict.value,
            findings=[str(f) for f in validation.findings],
        )

        say(f"\nVERDICT (attempt {attempt}): {validation.verdict.value.upper()}")
        say(summarize(validation))

        if validation.verdict is Verdict.APPROVED or attempt == 2:
            break

        reason = rejection_reason(validation)
        result.retried = True
        try:
            proposal = agent.revise(staged.context, proposal, reason)
        except SchemaFailure as exc:
            result.schema_failed = True
            say(f"SCHEMA FAILURE on retry: {exc}")
            break
        say("\nREVISED PROPOSAL:")
        _print_proposal(say, proposal, staged)

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
    args = parser.parse_args(argv)

    try:
        run_tick(
            args.scenario,
            profile=args.profile,
            runs_dir=args.runs_dir,
            config_path=args.config,
            seed=args.seed,
        )
    except ProviderError as exc:
        print(f"\nserver problem: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
