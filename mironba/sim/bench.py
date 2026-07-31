"""Measure what a live model actually does with the M1 prompts.

    python -m mironba.sim.bench --scenario configs/scenario/curry-to-lakers.yaml -n 20

Reports three numbers, each chosen because it can be misreported easily:

**Schema-failure rate.** Share of calls whose *first* attempt failed pydantic
validation. First attempt, not final — the repair retry is our mitigation, and
folding it in would measure the mitigation while calling it the model. The
unrecovered rate (failed twice) is reported separately.

**Legal-proposal rate is gone.** It is 100% by construction under M1.5 and
reporting it would be self-congratulation: the solver cannot emit an illegal
package, so measuring how many packages are legal measures nothing. What
replaces it is *intent satisfiability* — how often what the model wants is
achievable at all — which is the question the old number was badly proxying.

**Latency per call.** Mean and median wall-clock per HTTP round trip, on this
hardware, at this quantization and offload split. Not portable — the manifest
records the conditions so the number means something later.

Every trial is a real run with its own manifest and run id. Nothing here is
estimated or extrapolated; a trial that crashes is counted as a crash.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from mironba.llm.providers import ProviderError
from mironba.rules.trade_validator import Verdict
from mironba.sim.tick import run_tick, use_utf8_console


def bench(
    scenario_path: Path | str,
    trials: int,
    *,
    profile: str = "gm_agent",
    runs_dir: Path | str = "runs",
    vary_seed: bool = True,
    base_seed: int = 20260730,
) -> dict:
    """Run ``trials`` independent ticks and aggregate what happened."""
    verdict_first: Counter[str] = Counter()
    verdict_final: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    latencies: list[float] = []
    calls = failures = repairs_ok = gave_up = truncations = 0
    retried = 0
    run_ids: list[str] = []
    intents = satisfiable_first = satisfiable_final = 0
    revised = revision_rescued = 0
    packages_per_intent: list[int] = []
    decline_reasons: list[str] = []
    solver_times: list[float] = []
    binding: Counter[str] = Counter()

    for i in range(trials):
        print(f"\n----- trial {i + 1}/{trials} -----", flush=True)
        try:
            result, run, client = run_tick(
                scenario_path,
                profile=profile,
                runs_dir=runs_dir,
                quiet=True,
                # One seed per trial, recorded in that trial's manifest.
                seed=(base_seed + i) if vary_seed else None,
            )
        except ProviderError as exc:
            print(f"  server error: {exc}", flush=True)
            outcomes["server_error"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - a crash is a result too
            print(f"  crashed: {type(exc).__name__}: {exc}", flush=True)
            outcomes["crash"] += 1
            continue

        run_ids.append(run.run_id)
        calls += client.stats.calls
        failures += client.stats.first_attempt_failures
        repairs_ok += client.stats.repairs_succeeded
        gave_up += client.stats.gave_up
        truncations += client.stats.truncations
        latencies.extend(client.stats.latencies)

        if result.schema_failed:
            outcomes["schema_failed"] += 1
        elif result.stood_pat:
            outcomes["stood_pat"] += 1
        elif result.declined_all:
            outcomes["declined_all"] += 1
        elif result.final_intent_satisfiable is False:
            outcomes["unsatisfiable"] += 1
        else:
            outcomes["traded"] += 1

        if result.first_intent_satisfiable is not None:
            intents += 1
            if result.first_intent_satisfiable:
                satisfiable_first += 1
            if result.final_intent_satisfiable:
                satisfiable_final += 1
            if result.first_intent_satisfiable is False and result.retried:
                revised += 1
                if result.final_intent_satisfiable:
                    revision_rescued += 1
        if result.packages_offered:
            packages_per_intent.append(result.packages_offered)
        if result.declined_all:
            decline_reasons.append(result.decline_reason[:200])
        solver_times.extend(result.solver_seconds)
        binding.update(result.binding_constraints)

        if result.retried:
            retried += 1
        if result.verdicts:
            verdict_first[result.verdicts[0].value] += 1
            verdict_final[result.verdicts[-1].value] += 1

        summary = (
            result.final_verdict.value
            if result.final_verdict
            else (
                "stood_pat"
                if result.stood_pat
                else "declined_all"
                if result.declined_all
                else "unsatisfiable"
            )
        )
        print(
            f"  {summary}   packages={result.packages_offered} "
            f"retried={result.retried} run={run.run_id}",
            flush=True,
        )

    return _summarise(
        trials=trials,
        run_ids=run_ids,
        calls=calls,
        failures=failures,
        repairs_ok=repairs_ok,
        gave_up=gave_up,
        truncations=truncations,
        latencies=latencies,
        retried=retried,
        verdict_first=verdict_first,
        verdict_final=verdict_final,
        outcomes=outcomes,
        intents=intents,
        satisfiable_first=satisfiable_first,
        satisfiable_final=satisfiable_final,
        revised=revised,
        revision_rescued=revision_rescued,
        packages_per_intent=packages_per_intent,
        decline_reasons=decline_reasons,
        solver_times=solver_times,
        binding=binding,
    )


def _summarise(
    *,
    trials, run_ids, calls, failures, repairs_ok, gave_up, truncations,
    latencies, retried, verdict_first, verdict_final, outcomes, incomplete=0,
    intents=0, satisfiable_first=0, satisfiable_final=0, revised=0,
    revision_rescued=0, packages_per_intent=None, decline_reasons=None,
    solver_times=None, binding=None,
) -> dict:
    """One place that turns counters into rates, shared by both entry points.

    Shared so the live path and the from-disk path cannot drift into reporting
    the same label two different ways.
    """
    packages_per_intent = packages_per_intent or []
    decline_reasons = decline_reasons or []
    solver_times = solver_times or []
    binding = binding or Counter()
    return {
        "trials": trials,
        "completed": len(run_ids),
        "incomplete": incomplete,
        "calls": calls,
        "schema_failure_rate_first_attempt": round(failures / calls, 4) if calls else None,
        "first_attempt_failures": failures,
        "repairs_succeeded": repairs_ok,
        "unrecovered_schema_failures": gave_up,
        "unrecovered_rate": round(gave_up / calls, 4) if calls else None,
        "truncations": truncations,
        "mean_latency_s": round(statistics.fmean(latencies), 2) if latencies else None,
        "median_latency_s": round(statistics.median(latencies), 2) if latencies else None,
        "p90_latency_s": (
            round(sorted(latencies)[int(0.9 * (len(latencies) - 1))], 2)
            if latencies else None
        ),
        # Intent satisfiability replaces the legal-proposal rate, which is
        # 100% by construction and therefore not a finding.
        "intents": intents,
        "intent_satisfiable_first": (
            round(satisfiable_first / intents, 4) if intents else None
        ),
        "intent_satisfiable_final": (
            round(satisfiable_final / intents, 4) if intents else None
        ),
        "revised_intents": revised,
        "revisions_that_became_satisfiable": revision_rescued,
        "revision_rescue_rate": (
            round(revision_rescued / revised, 4) if revised else None
        ),
        "mean_packages_per_satisfiable_intent": (
            round(statistics.fmean(packages_per_intent), 2)
            if packages_per_intent else None
        ),
        "declined_all_count": len(decline_reasons),
        "decline_reasons": decline_reasons[:6],
        "solver_p50_s": (
            round(statistics.median(solver_times), 4) if solver_times else None
        ),
        "solver_worst_s": round(max(solver_times), 4) if solver_times else None,
        "binding_constraints": dict(binding.most_common()),
        "verdicts_first": dict(verdict_first),
        "verdicts_final": dict(verdict_final),
        "outcomes": dict(outcomes),
        "trials_that_retried": retried,
        "run_ids": run_ids,
    }


def aggregate_runs(runs_dir: Path | str, scenario_id: str | None = None) -> dict:
    """Recompute the same metrics from run artifacts on disk.

    The payoff of writing a manifest before the first token: a bench that dies
    part-way — killed, crashed, timed out — has still left a complete, readable
    record of every trial that finished. Re-deriving from disk is not a
    fallback so much as proof that the artifacts are the source of truth and
    the in-memory aggregation is a convenience.

    A run directory without ``stats.json`` did not finish, and is counted as
    incomplete rather than quietly skipped.
    """
    verdict_first: Counter[str] = Counter()
    verdict_final: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    latencies: list[float] = []
    calls = failures = repairs_ok = gave_up = truncations = 0
    retried = 0
    run_ids: list[str] = []
    incomplete = 0
    intents = satisfiable_first = satisfiable_final = 0
    revised = revision_rescued = 0
    packages_per_intent: list[int] = []
    decline_reasons: list[str] = []
    solver_times: list[float] = []
    binding: Counter[str] = Counter()

    for directory in sorted(Path(runs_dir).iterdir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if scenario_id and manifest.get("scenario_id") != scenario_id:
            continue
        if not (directory / "stats.json").is_file():
            incomplete += 1
            continue

        run_ids.append(manifest["run_id"])
        for row in _jsonl(directory / "llm_calls.jsonl"):
            calls += 1 if row["attempt"] == 0 else 0
            latencies.append(row["latency_s"])
            if row["attempt"] == 0 and not row["ok"]:
                failures += 1
            if row["attempt"] == 1 and row["ok"]:
                repairs_ok += 1
            if row["attempt"] == 1 and not row["ok"]:
                gave_up += 1
            if row.get("truncated"):
                truncations += 1

        verdicts = [
            e["payload"]["verdict"]
            for e in _jsonl(directory / "events.jsonl")
            if e["type"] == "rules.verdict"
        ]
        finished = [
            e for e in _jsonl(directory / "events.jsonl") if e["type"] == "run.finished"
        ]
        payload = finished[-1]["payload"] if finished else {}
        if payload.get("retried"):
            retried += 1
        first_ok = payload.get("first_intent_satisfiable")
        final_ok = payload.get("final_intent_satisfiable")
        if first_ok is not None:
            intents += 1
            satisfiable_first += 1 if first_ok else 0
            satisfiable_final += 1 if final_ok else 0
            if not first_ok and payload.get("retried"):
                revised += 1
                revision_rescued += 1 if final_ok else 0
        if payload.get("packages_offered"):
            packages_per_intent.append(payload["packages_offered"])
        if payload.get("declined_all"):
            decline_reasons.append(str(payload.get("decline_reason", ""))[:200])
        solver_times.extend(payload.get("solver_seconds") or [])
        binding.update(payload.get("binding_constraints") or [])
        if payload.get("schema_failed"):
            outcomes["schema_failed"] += 1
        elif payload.get("stood_pat"):
            outcomes["stood_pat"] += 1
        elif payload.get("malformed") and not verdicts:
            outcomes["malformed_only"] += 1
        else:
            outcomes["proposed"] += 1
        if verdicts:
            verdict_first[verdicts[0]] += 1
            verdict_final[verdicts[-1]] += 1

    return _summarise(
        trials=len(run_ids) + incomplete,
        run_ids=run_ids,
        calls=calls,
        failures=failures,
        repairs_ok=repairs_ok,
        gave_up=gave_up,
        truncations=truncations,
        latencies=latencies,
        retried=retried,
        verdict_first=verdict_first,
        verdict_final=verdict_final,
        outcomes=outcomes,
        incomplete=incomplete,
        intents=intents,
        satisfiable_first=satisfiable_first,
        satisfiable_final=satisfiable_final,
        revised=revised,
        revision_rescued=revision_rescued,
        packages_per_intent=packages_per_intent,
        decline_reasons=decline_reasons,
        solver_times=solver_times,
        binding=binding,
    )


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def format_report(stats: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    def secs(x):
        return "n/a" if x is None else f"{x:.2f}s"

    return "\n".join(
        [
            "",
            "=" * 62,
            f"  trials {stats['completed']}/{stats['trials']} completed"
            + (f" ({stats['incomplete']} incomplete)" if stats.get("incomplete") else "")
            + f", {stats['calls']} LLM calls",
            "=" * 62,
            f"  schema failure (1st attempt)   {pct(stats['schema_failure_rate_first_attempt'])}"
            f"   ({stats['first_attempt_failures']}/{stats['calls']})",
            f"  unrecovered after repair       {pct(stats['unrecovered_rate'])}"
            f"   ({stats['unrecovered_schema_failures']})",
            f"  truncated completions          {stats['truncations']}",
            "",
            f"  intent satisfiable (1st)       {pct(stats['intent_satisfiable_first'])}"
            f"   ({stats['intents']} intents)",
            f"  intent satisfiable (final)     {pct(stats['intent_satisfiable_final'])}",
            f"  revisions that rescued         {stats['revisions_that_became_satisfiable']}"
            f"/{stats['revised_intents']}"
            f"   ({pct(stats['revision_rescue_rate'])})",
            f"  packages per satisfiable       {stats['mean_packages_per_satisfiable_intent']}",
            f"  declined all legal options     {stats['declined_all_count']}",
            "",
            f"  solver p50 / worst             {secs(stats['solver_p50_s'])}"
            f" / {secs(stats['solver_worst_s'])}",
            f"  binding constraints            {stats['binding_constraints']}",
            "",
            f"  latency mean / median / p90    {secs(stats['mean_latency_s'])}"
            f" / {secs(stats['median_latency_s'])} / {secs(stats['p90_latency_s'])}",
            "",
            f"  verdicts first attempt         {stats['verdicts_first']}",
            f"  verdicts final                 {stats['verdicts_final']}",
            f"  outcomes                       {stats['outcomes']}",
            "=" * 62,
        ]
    )


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="Measure M1 against a live model.")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--from-runs", type=Path, default=None,
                        help="aggregate existing run artifacts instead of calling the model")
    parser.add_argument("--scenario-id", default=None,
                        help="with --from-runs, restrict to one scenario id")
    parser.add_argument("-n", "--trials", type=int, default=20)
    parser.add_argument("--profile", default="gm_agent")
    parser.add_argument("--runs-dir", default="runs", type=Path)
    parser.add_argument("--out", type=Path, default=None, help="write JSON stats here")
    parser.add_argument("--probe", action="store_true",
                        help="probe schema enforcement and exit")
    parser.add_argument("--fixed-seed", action="store_true",
                        help="reuse one seed for every trial (measures determinism, not the distribution)")
    args = parser.parse_args(argv)

    if args.probe:
        import json as _json

        from mironba.llm.client import load_config, resolve_profile
        from mironba.llm.probe import probe_schema_enforcement

        cfg = resolve_profile(load_config(), args.profile)
        print(_json.dumps(probe_schema_enforcement(cfg), indent=2))
        return 0

    if args.from_runs:
        stats = aggregate_runs(args.from_runs, args.scenario_id)
    else:
        if not args.scenario:
            parser.error("--scenario is required unless --from-runs is given")
        stats = bench(
            args.scenario, args.trials, profile=args.profile,
            runs_dir=args.runs_dir, vary_seed=not args.fixed_seed,
        )
    print(format_report(stats))
    if args.out:
        import json

        args.out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0 if stats["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
