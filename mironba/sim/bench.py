"""Measure what a live model actually does with the M1 prompts.

    python -m mironba.sim.bench --scenario configs/scenario/curry-to-lakers.yaml -n 20

Reports three numbers, each chosen because it can be misreported easily:

**Schema-failure rate.** Share of calls whose *first* attempt failed pydantic
validation. First attempt, not final — the repair retry is our mitigation, and
folding it in would measure the mitigation while calling it the model. The
unrecovered rate (failed twice) is reported separately.

**Illegal-proposal rate, before and after the retry.** Before is how often a
well-formed proposal is rejected by ``rules/``. After is how often it is still
rejected once the CBA's objection has been handed back. The gap between them is
the only thing in M1 that says whether feeding a rejection reason back does
anything at all.

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
        elif result.malformed and not result.verdicts:
            outcomes["malformed_only"] += 1
        else:
            outcomes["proposed"] += 1

        if result.retried:
            retried += 1
        if result.verdicts:
            verdict_first[result.verdicts[0].value] += 1
            verdict_final[result.verdicts[-1].value] += 1

        summary = (
            result.final_verdict.value if result.final_verdict else
            ("stood_pat" if result.stood_pat else "no verdict")
        )
        print(
            f"  {summary}   malformed={result.malformed} "
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
    )


def _summarise(
    *,
    trials, run_ids, calls, failures, repairs_ok, gave_up, truncations,
    latencies, retried, verdict_first, verdict_final, outcomes, incomplete=0,
) -> dict:
    """One place that turns counters into rates, shared by both entry points.

    Shared so the live path and the from-disk path cannot drift into reporting
    the same label two different ways.
    """
    judged_first = sum(verdict_first.values())
    judged_final = sum(verdict_final.values())
    illegal_before = verdict_first.get("rejected", 0)
    illegal_after = verdict_final.get("rejected", 0)
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
        "illegal_proposal_rate_before_retry": (
            round(illegal_before / judged_first, 4) if judged_first else None
        ),
        "illegal_proposal_rate_after_retry": (
            round(illegal_after / judged_final, 4) if judged_final else None
        ),
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
            f"  illegal before retry           {pct(stats['illegal_proposal_rate_before_retry'])}",
            f"  illegal after retry            {pct(stats['illegal_proposal_rate_after_retry'])}",
            f"  trials that retried            {stats['trials_that_retried']}",
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
    parser.add_argument("--fixed-seed", action="store_true",
                        help="reuse one seed for every trial (measures determinism, not the distribution)")
    args = parser.parse_args(argv)

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
