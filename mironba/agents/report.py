"""The ReportAgent: prose over a run, with the claims it cannot make removed.

    python -m mironba.agents.report runs/<run-id>

Every other agent in this codebase is constrained by making illegal output
unrepresentable — a GM names an index, not a package, so it cannot propose an
illegal trade. A report is prose, and prose has no schema that can enforce
"do not oversell". So the constraint is applied twice, and neither time by
asking the model nicely:

1. **The numbers are not generated.** Precision, recall and the limitation
   block are assembled deterministically from the scoring output and appended
   to whatever the model wrote. The model is never asked to state a figure, so
   it cannot state one wrong. ``LIMITATIONS`` is a module constant.

2. **The prose is filtered.** ``FORBIDDEN`` matches the two claim shapes this
   agent is not allowed to make — presenting a simulated outcome as a
   prediction, and ranking options the value model cannot separate. Any
   sentence that trips it is dropped and counted, and the count is reported.

The second is a blunt instrument and will sometimes drop an innocent sentence.
That is the intended direction of error: a report that reads as a working
predictor misrepresents a project whose headline precision is 1 in 421, and
the cost of over-filtering is a duller paragraph.

The report agent runs on the deep profile because it is the one place where
fluency matters and there is exactly one call per run.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from mironba.world.manifest import template_hash
from mironba.report import use_utf8_stdout
from mironba.report.timeline import Feed, load_run

#: Always present, verbatim, in every report this agent produces. Appended
#: after the model's prose rather than requested from it, so no prompt failure
#: or schema drift can omit it. ``test_limitations_always_present`` asserts
#: every line survives into the rendered output.
LIMITATIONS = (
    "This is a simulation, not a forecast. Nothing above is a prediction.",
    "The deadline planner's measured precision is 1 in 421 proposals.",
    "Predictive recall on non-stipulated signings is 0 of 1.",
    "A counterfactual branch has no ground truth and is never scored.",
    "Win deltas carry a measured error of 10.48 wins, so options closer than "
    "10.5 wins apart are not ranked.",
    "Trades are two-team only; real deadline business is often multi-team.",
)

#: Claim shapes this agent may not make. Deliberately over-broad.
FORBIDDEN = (
    # A simulated outcome presented as a forecast.
    re.compile(r"\b(will|would|going to)\s+(sign|trade|win|land|acquire|end up)\b", re.I),
    re.compile(r"\b(predicts?|prediction|forecasts?|expect(s|ed)?\s+to|likely to)\b", re.I),
    re.compile(r"\b(proves?|demonstrates?|shows?)\s+that\s+\w+\s+(is|are|will)\b", re.I),
    # A ranking the value model cannot support.
    re.compile(r"\b(better|worse|stronger|weaker|superior|inferior)\s+(than|team|option|move)\b", re.I),
    re.compile(r"\b(best|worst|optimal|ideal)\s+(move|trade|option|outcome|fit)\b", re.I),
    re.compile(r"\bshould\s+(have\s+)?(trade|sign|acquire|pursue|target)\b", re.I),
)


class BranchSummary(BaseModel):
    """Small on purpose — the charter's two-step rule. The model describes what
    happened; it is never asked for a number or a judgement."""

    what_happened: str = Field(
        description="Two or three sentences on what the teams did in this branch. "
        "Describe actions only. No evaluation, no comparison, no prediction."
    )
    consequences: list[str] = Field(
        default_factory=list,
        description="Moves that followed from an earlier move, as "
        "'X happened, after which Y happened'. Only where the event log shows "
        "the ordering. Empty list if none.",
    )


@dataclass
class Report:
    run_id: str
    branches: dict[str, BranchSummary] = field(default_factory=dict)
    scores: dict = field(default_factory=dict)
    dropped: list[str] = field(default_factory=list)
    unfalsifiable: tuple[str, ...] = ()

    def render(self, width: int = 78) -> str:
        lines = ["=" * width, f"  REPORT  {self.run_id}", "=" * width]
        for name, summary in self.branches.items():
            tag = " [COUNTERFACTUAL - UNFALSIFIABLE, NOT SCORED]" if name in self.unfalsifiable else " [ACTUAL]"
            lines += ["", f"  {name}{tag}", "  " + "-" * (width - 4)]
            for para in _wrap(summary.what_happened, width - 4):
                lines.append(f"  {para}")
            if summary.consequences:
                lines.append("")
                lines.append("  Consequences the event log supports:")
                for item in summary.consequences:
                    for i, para in enumerate(_wrap(item, width - 8)):
                        lines.append(f"    {'- ' if i == 0 else '  '}{para}")
        if self.scores:
            lines += ["", "  WHAT SCORED", "  " + "-" * (width - 4)]
            for key, value in self.scores.items():
                lines.append(f"    {key:<38} {value}")
        lines += ["", "  LIMITATIONS", "  " + "-" * (width - 4)]
        for item in LIMITATIONS:
            for i, para in enumerate(_wrap(item, width - 8)):
                lines.append(f"    {'- ' if i == 0 else '  '}{para}")
        if self.dropped:
            lines += [
                "",
                f"  {len(self.dropped)} sentence(s) removed by the claim filter:",
            ]
            for text in self.dropped:
                for i, para in enumerate(_wrap(text, width - 8)):
                    lines.append(f"    {'! ' if i == 0 else '  '}{para}")
        lines.append("=" * width)
        return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text.strip(), width) or [""]


def filter_claims(text: str) -> tuple[str, list[str]]:
    """Drop sentences making a claim this agent may not make.

    Returns the surviving prose and the dropped sentences. Dropped text is
    *reported*, never silently discarded — a filter that hides its own action
    is indistinguishable from a model that never overstepped.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept, dropped = [], []
    for sentence in sentences:
        if not sentence.strip():
            continue
        if any(pattern.search(sentence) for pattern in FORBIDDEN):
            dropped.append(sentence.strip())
        else:
            kept.append(sentence.strip())
    return " ".join(kept), dropped


def feed_digest(feed: Feed, limit: int = 40) -> str:
    """What the model is shown: the rendered feed, no salaries anywhere.

    The same boundary as every other agent. A report agent that could see cap
    figures would be able to state terms, and stating terms is exactly what the
    architecture spent M1 making impossible.
    """
    lines = []
    for entry in feed.entries[:limit]:
        line = f"[{entry.actor}] {entry.headline}"
        if entry.reasoning:
            line += f" | stated reason: {entry.reasoning[:160]}"
        lines.append(line)
    return "\n".join(lines)


SYSTEM = (
    "You summarise what happened in a simulation run. You describe actions "
    "taken and their stated reasons. You never predict, never rank options, "
    "never say one team or move is better than another, and never present a "
    "simulated outcome as something that will happen. You have no access to "
    "salaries or contract terms and must not state any figure."
)


class ReportAgent:
    def __init__(self, client, profile: str = "report_agent") -> None:
        self.client = client
        self.profile = profile

    def summarise_branch(self, name: str, feed: Feed) -> tuple[BranchSummary, list[str]]:
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Branch: {name}\n\nEvent feed:\n{feed_digest(feed)}\n\n"
                    "Describe what happened in this branch, and list only those "
                    "consequences the ordering above actually supports."
                ),
            },
        ]
        summary = self.client.complete(
            messages, schema=BranchSummary, profile=self.profile, purpose="report"
        )
        clean, dropped = filter_claims(summary.what_happened)
        kept_consequences = []
        for item in summary.consequences:
            text, gone = filter_claims(item)
            dropped.extend(gone)
            if text:
                kept_consequences.append(text)
        return BranchSummary(what_happened=clean, consequences=kept_consequences), dropped


def build_report(
    run_id: str,
    branches: dict[str, Feed],
    scores: dict | None = None,
    agent: ReportAgent | None = None,
    unfalsifiable: tuple[str, ...] = (),
) -> Report:
    """Assemble a report. Works without a model — the deterministic half is the
    half that carries the numbers, so a run with no LLM available still
    produces a correct, if terse, report."""
    report = Report(run_id=run_id, scores=dict(scores or {}), unfalsifiable=unfalsifiable)
    for name, feed in branches.items():
        if agent is None:
            refusals = len(feed.refusals)
            report.branches[name] = BranchSummary(
                what_happened=(
                    f"{len(feed.entries)} events, {refusals} of them refusals or "
                    f"failures. No model was available, so this branch is "
                    f"described by its event counts only."
                ),
                consequences=[],
            )
            continue
        summary, dropped = agent.summarise_branch(name, feed)
        report.branches[name] = summary
        report.dropped.extend(dropped)
    return report



# ---------------------------------------------------------------------------
# Summarising a MANIFEST rather than an event log
# ---------------------------------------------------------------------------
#
# The agent read event feeds only, and a stipulated run writes no event log -
# so exactly the run kind the UI creates was the one kind it could not
# describe. 343 of 408 recorded runs carry an event log and 7 do not, and the
# 7 are the ones a user makes. The manifest carries everything those runs
# recorded, so the agent reads that instead.
#
# The constraint is tighter here than for a feed. A manifest is numbers, and
# prose about numbers is where a summary invents. So the digest below states
# every figure the model is allowed to use, and ``manifest_numbers`` is the
# closed set a test checks the prose against: any number in the output that
# is not in the manifest is a claim the run did not make.


def manifest_numbers(manifest: dict) -> set:
    """Every number the manifest supports, as strings.

    Literal values, plus the LENGTH of every list. A count of things the
    manifest enumerates is not a new claim - "9 generated trades" is a fact
    about a list of nine trades even though the digit 9 appears nowhere in
    the file. Anything outside this set that turns up in the prose was
    invented.
    """
    out: set = set()

    def note(value) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, int):
            out.add(str(value))
        elif isinstance(value, float):
            out.add(str(value))
            if value.is_integer():
                out.add(str(int(value)))
        elif isinstance(value, str):
            # \b so "curryst01" contributes nothing. Without it every
            # player id donated its suffix and the allowed set quietly
            # absorbed 00-99 - which would have let the prose invent any
            # small number and still pass the test that exists to stop it.
            for token in re.findall(r"\b\d[\d,]*(?:\.\d+)?\b", value):
                out.add(token.replace(",", ""))

    def walk(node) -> None:
        if isinstance(node, dict):
            # "30 of 30 teams" is a fact about a mapping of thirty teams,
            # exactly as a list length is a fact about a list.
            note(len(node))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            note(len(node))
            for value in node:
                walk(value)
        else:
            note(node)

    walk(manifest)
    return out


def manifest_digest(manifest: dict) -> str:
    """What the model is given: the run's own figures, and nothing else."""
    cascade = manifest.get("cascade") or {}
    reaction = manifest.get("reaction") or {}
    duties = manifest.get("obligations") or {}
    seed = manifest.get("trade") or manifest.get("signing") or {}
    contests = [c for c in (manifest.get("contests") or [])
                if c.get("contested")]
    arbitrary = [c for c in contests if "arbitrary" in str(c.get("reason", ""))]
    movers = [t for t, row in reaction.items()
              if row.get("signed") or row.get("lost_contests")]

    lines = [
        f"scenario: {manifest.get('scenario', '')}",
        f"seed kind: {manifest.get('seed_kind', 'trade')}",
        f"seed event: {seed.get('label', '(none recorded)')}",
        f"seed legal: {seed.get('legal')}",
        "",
        f"generated trades: {len(cascade.get('seeded_trades', []))}",
        f"generated trades WITHOUT the seed: "
        f"{len(cascade.get('unseeded_trades', []))}",
        f"attributable to the seed: "
        f"{len(cascade.get('attributable_to_seed', []))}",
        f"displaced by the seed: "
        f"{len(cascade.get('displaced_by_seed', []))}",
        f"cascade depth reached: {cascade.get('depth_reached')}",
        f"killed by the counterparty gate: "
        f"{cascade.get('killed_by_counterparty_gate')}",
        f"killed by the solver: {cascade.get('killed_by_solver')}",
        "",
        f"teams that moved: {len(movers)} of {len(reaction)}",
        f"teams the rules forced to act: "
        f"{len(duties.get('teams_forced', []) or [])}",
        f"contested players: {len(contests)}, of which "
        f"{len(arbitrary)} resolved on an arbitrary tiebreak and carry no "
        "signal",
        f"teams that signed differently from the unseeded run: "
        f"{len(cascade.get('signings_changed', []) or [])}",
    ]
    return "\n".join(lines)


MANIFEST_SYSTEM = (
    "You describe what a recorded simulation run contains. You are given the "
    "run's own figures and nothing else.\n"
    "RULES:\n"
    "- Use ONLY the numbers given. Do not compute new ones, do not round, do "
    "not estimate, do not infer a number that is not listed.\n"
    "- This is a simulation, never a forecast. Never say what will, would or "
    "should happen.\n"
    "- Never rank teams or moves as better, worse, best or optimal.\n"
    "- A trade that also happens without the seed is not caused by the seed. "
    "An arbitrary tiebreak carries no signal.\n"
    "- Two short paragraphs at most. Say what the run recorded."
)


def summarise_manifest(agent, run_id: str, manifest: dict):
    """One BranchSummary for a manifest-only run, filtered like any other."""
    messages = [
        {"role": "system", "content": MANIFEST_SYSTEM},
        {"role": "user", "content":
            f"Run: {run_id}\n\nRecorded figures:\n"
            f"{manifest_digest(manifest)}\n\n"
            "Describe what this run recorded."},
    ]
    summary = agent.client.complete(
        messages, schema=BranchSummary, profile=agent.profile,
        purpose="report_manifest",
    )
    clean, dropped = filter_claims(summary.what_happened)
    kept = []
    for item in summary.consequences:
        text, gone = filter_claims(item)
        dropped.extend(gone)
        if text:
            kept.append(text)
    return BranchSummary(what_happened=clean, consequences=kept), dropped


def build_manifest_report(run_id: str, manifest: dict,
                          agent=None) -> "Report":
    """A report for a run that recorded a manifest and no event log."""
    unfalsifiable = ((run_id,) if manifest.get("unfalsifiable") else ())
    report = Report(run_id=run_id, unfalsifiable=unfalsifiable)
    if agent is None:
        cascade = manifest.get("cascade") or {}
        report.branches[run_id] = BranchSummary(
            what_happened=(
                f"{len(cascade.get('seeded_trades', []))} generated trade(s), "
                f"{len(cascade.get('attributable_to_seed', []))} attributable "
                "to the seed. No model was available, so this run is "
                "described by its recorded counts only."),
            consequences=[])
        return report
    summary, dropped = summarise_manifest(agent, run_id, manifest)
    report.branches[run_id] = summary
    report.dropped.extend(dropped)
    return report


def report_client(profile: str = "report_agent", runs_dir: Path | None = None):
    """A client under its own manifest.

    A report is an artifact like any other, so it gets a run id, a model id and
    a code revision. Rendering the same events through a different model must
    be distinguishable after the fact, which is the whole reason the manifest
    rule exists.
    """
    from mironba.llm.client import (
        LLMClient,
        load_config,
        probe_model,
        probe_runtime,
        resolve_profile,
    )
    from mironba.world.manifest import Run, build_manifest

    config = load_config()
    cfg = resolve_profile(config, profile)
    info = probe_model(cfg)
    runtime = probe_runtime(cfg)
    manifest = build_manifest(
        model_id=cfg.model,
        server=cfg.server,
        base_url=cfg.base_url,
        quantization=info.quantization,
        prompt_template_hash=template_hash(SYSTEM),
        snapshot_date="n/a - reads an existing run's events",
        temperature=cfg.temperature,
        top_p=cfg.top_p,
        seed=cfg.seed,
        thinking=cfg.thinking,
        profile=cfg.name,
        scenario_id="report",
        model_size_bytes=runtime.size_bytes,
        model_size_vram_bytes=runtime.size_vram_bytes,
        gpu_fraction=runtime.gpu_fraction,
        fully_resident=runtime.fully_resident,
        notes="ReportAgent: prose over an existing event log.",
    )
    run = Run.start(manifest, runs_dir=runs_dir) if runs_dir else Run.start(manifest)
    return ReportAgent(LLMClient(run, config=config), profile=profile), run


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", help="run directory containing events.jsonl")
    parser.add_argument("--no-llm", action="store_true", help="deterministic half only")
    args = parser.parse_args(argv)
    use_utf8_stdout()

    run_dir = Path(args.run)
    agent = None
    if not args.no_llm:
        try:
            agent, _ = report_client()
        except Exception as exc:  # noqa: BLE001
            print(f"  (no model available: {exc}; deterministic half only)")

    # A stipulated run writes a manifest and no event log. Reading the
    # manifest is not a fallback for a missing feed - it is the only record
    # those runs have, and they are the kind the UI creates.
    if (run_dir / "events.jsonl").is_file():
        report = build_report(run_dir.name, {"run": load_run(args.run)},
                              agent=agent)
    else:
        manifest_path = run_dir / "manifest.json"
        if not manifest_path.is_file():
            raise SystemExit(
                f"{run_dir} has neither events.jsonl nor manifest.json; "
                "there is nothing recorded to summarise")
        import json as _json

        report = build_manifest_report(
            run_dir.name,
            _json.loads(manifest_path.read_text(encoding="utf-8")),
            agent=agent)
    print(report.render())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
