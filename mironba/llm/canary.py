"""A fixed-prompt throughput measurement, recorded in every manifest.

Third time a measurement was trusted as a guarantee it never made:

  M1    ``schema_enforced_by_server: true`` recorded that we *sent* a schema.
        The server ignored it.
  M1.5  ``enforces_schema()`` returned a hardcoded answer about a capability
        that turned out to be version-dependent.
  M2    ``gpu_fraction: 1.0`` was true — the weights really were entirely in
        VRAM — while throughput sat at 12 tok/s instead of 35, because
        background processes had taken the card to 23.5 of 24.6 GiB and left
        no room for compute buffers.

Residency is a statement about where the weights are. It says nothing about
whether there is headroom left to run them in, and the failure is silent in
exactly the way that matters: the manifest looks correct, the run completes,
and only the latency column is wrong. A 3x slowdown is not a latency footnote
when latency is one of the reported numbers.

The fix is to stop inferring speed and measure it. One fixed prompt, greedy,
no schema, same token budget every time, run before the manifest is minted.
The number goes in the manifest next to ``gpu_fraction``, and a bench refuses
to start when it drifts from a recorded baseline.

Deliberately measures the *generation* rate from the server's own
``eval_count`` / ``eval_duration`` rather than wall clock. Wall clock folds in
model loading, which is legitimately variable and would make the canary fire on
a cold start. Wall time is recorded alongside anyway, because a large gap
between the two is itself a symptom — under memory pressure this machine showed
42s of wall for 13.7s of generation.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from mironba.llm.providers import ProviderError, SamplingParams, provider_for

#: Fixed forever. A canary whose prompt changes measures two things at once.
#: Chosen to generate a predictable number of cheap tokens with no reasoning:
#: the point is to load the sampler and the attention path, not to be a task.
CANARY_PROMPT = "Count from 1 to 60, one number per line."

#: Enough tokens for the rate to be stable, few enough to stay cheap. At the
#: healthy ~37 tok/s on this hardware that is roughly five seconds.
CANARY_TOKENS = 200

#: Fractional deviation from baseline that aborts a bench.
DEFAULT_TOLERANCE = 0.15

BASELINE_PATH = Path("configs/throughput_baseline.json")


class ThroughputError(RuntimeError):
    """Measured throughput is off baseline. Never downgraded to a warning."""


@dataclass(frozen=True, slots=True)
class ThroughputSample:
    tokens_per_s: float
    wall_s: float
    generated_tokens: int

    @property
    def overhead_ratio(self) -> float:
        """Wall time over generation time. Large means something is thrashing."""
        generation = self.generated_tokens / self.tokens_per_s if self.tokens_per_s else 0
        return (self.wall_s / generation) if generation else 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["overhead_ratio"] = round(self.overhead_ratio, 2)
        return data


def measure_throughput(cfg, *, tokens: int = CANARY_TOKENS) -> ThroughputSample:
    """Run the canary prompt and report the generation rate."""
    provider = provider_for(cfg.server)
    started = time.monotonic()
    completion = provider.chat(
        base_url=cfg.base_url,
        model=cfg.model,
        messages=[{"role": "user", "content": CANARY_PROMPT}],
        schema=None,
        params=SamplingParams(
            temperature=0.0,
            top_p=1.0,
            seed=0,
            max_tokens=tokens,
            context_length=cfg.context_length,
            thinking=False,
        ),
        timeout=cfg.request_timeout_s,
    )
    wall = time.monotonic() - started

    usage = completion.usage or {}
    generated = usage.get("completion_tokens") or 0
    raw = completion.raw or {}
    eval_ns = raw.get("eval_duration") or 0
    if not generated or not eval_ns:
        # No server-side timings: fall back to wall clock and say so by way of
        # an overhead ratio of exactly 1.0, rather than silently reporting a
        # rate that means something different from every other sample.
        rate = (generated / wall) if wall else 0.0
        return ThroughputSample(
            tokens_per_s=round(rate, 2),
            wall_s=round(wall, 2),
            generated_tokens=generated,
        )
    return ThroughputSample(
        tokens_per_s=round(generated / (eval_ns / 1e9), 2),
        wall_s=round(wall, 2),
        generated_tokens=generated,
    )


def _key(cfg) -> str:
    return f"{cfg.server}|{cfg.model}"


def load_baseline(path: Path | str = BASELINE_PATH) -> dict:
    path = Path(path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_baseline(cfg, sample: ThroughputSample, *, path: Path | str = BASELINE_PATH) -> None:
    path = Path(path)
    data = load_baseline(path)
    data[_key(cfg)] = {
        "tokens_per_s": sample.tokens_per_s,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prompt": CANARY_PROMPT,
        "tokens": CANARY_TOKENS,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def check_throughput(
    cfg,
    sample: ThroughputSample,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    path: Path | str = BASELINE_PATH,
) -> str | None:
    """Return an abort message when the sample is off baseline, else None.

    Deviation in *either* direction aborts. Slower is the case that motivated
    this. Faster matters too, in a quieter way: it means the stored baseline no
    longer describes this machine, so every previous comparison against it was
    against the wrong number. Both want a human decision, not a default.
    """
    baseline = load_baseline(path).get(_key(cfg))
    if not baseline:
        return None
    expected = baseline["tokens_per_s"]
    if not expected:
        return None
    drift = (sample.tokens_per_s - expected) / expected
    # Epsilon because the boundary is documented as inclusive and binary
    # floating point does not respect that: 36.0 * (1 - 0.15) is
    # 30.599999999999998, whose drift is -0.15000000000000002 and would abort a
    # bench that is exactly at tolerance.
    if abs(drift) <= tolerance + 1e-9:
        return None
    direction = "slower" if drift < 0 else "faster"
    return (
        f"throughput canary is {abs(drift):.0%} {direction} than baseline: "
        f"{sample.tokens_per_s} tok/s against {expected} tok/s recorded "
        f"{baseline.get('recorded_at', '?')}.\n"
        f"  wall {sample.wall_s}s for {sample.generated_tokens} tokens "
        f"(overhead ratio {sample.overhead_ratio:.1f}x)\n"
        "  gpu_fraction says where the weights are, not whether there is room "
        "to run them. Check for other processes holding VRAM.\n"
        "  Re-baseline deliberately with: python -m mironba.llm.canary "
        "--set-baseline"
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    from mironba.llm.client import load_config, resolve_profile

    parser = argparse.ArgumentParser(description="Measure and check throughput.")
    parser.add_argument("--profile", default="gm_agent")
    parser.add_argument("--set-baseline", action="store_true")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    cfg = resolve_profile(load_config(), args.profile)
    try:
        sample = measure_throughput(cfg)
    except ProviderError as exc:
        print(f"canary failed: {exc}")
        return 2

    print(f"{cfg.model} on {cfg.server}: {sample.tokens_per_s} tok/s "
          f"({sample.generated_tokens} tokens, wall {sample.wall_s}s, "
          f"overhead {sample.overhead_ratio:.1f}x)")

    if args.set_baseline:
        save_baseline(cfg, sample)
        print(f"baseline written to {BASELINE_PATH}")
        return 0

    problem = check_throughput(cfg, sample, tolerance=args.tolerance)
    if problem:
        print("\n" + problem)
        return 1
    print("within tolerance of baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
