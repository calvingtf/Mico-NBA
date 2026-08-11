# Results that weren't

Seven results that did not survive their own checks, and what each one turned out to be.

[← back to the README](../README.md)

Every number here appears beside what a random or do-nothing system scores on the same data. Including the ones that lost.

---

## Seven results that weren't

How this project's numbers get made — one metric's full life, from first
belief to promoted headline, each value believed when it was written:

![The correction chain: 12.4% with a leak open, 24.0% when fixing one leak exposed a second, 9.6% clean, 11.2% after the interaction was promoted against its own null](docs/figures/correction-chain.svg)


Every headline number below survived at least one revision, and seven were
wrong at the time they were first written down. They are listed because the
pattern is the most transferable thing this project produced — more so than
anything about basketball.

**Recall of 200%.** `pair_hits` counted *proposals* that matched a real trade;
recall divided it by the number of *real trades*. Several proposals can hit one
trade, so the ratio exceeded 1. Caught by printing the per-season table and
reading a percentage above 100 — the arithmetic had never been looked at, only
the trend.

**Validator legality of 5 of 5.** `TradeCheck.legal` counted `UNDETERMINED` as
legal. Rebuilding the verdict distribution gave APPROVED 0, UNDETERMINED 7,
REJECTED 3 — **no real trade is ever approved outright**. Worse, every trade in
that set was legal, so a validator approving everything scores 100%: the metric
was a false-rejection rate against a null it had never been compared to.

**Counterparty matching, 11 of 13.** Never tested against a null. The proposals
cover ~48% of all 435 team pairs, and a three-team trade is matched by any of
its three constituent pairs, so chance alone scores 10.18. **p = 0.426.** A
20,000-trial Monte Carlo put the observed value inside the null distribution's
bulk. Caught by asking what a random proposer would score.

**A canary recorded as passing on a hosted model.** The throughput canary exists
to catch a *local* server that has silently spilled weights to system RAM. The
first hosted run recorded `canary_tokens_per_s=102.56` — a real number,
measuring network latency and someone else's load, that would have read as a
pass. **A canary that cannot fail is not a check.** Caught by reading the
manifest of a run rather than its summary line.

**Sampling parameters recorded as values never sent.** Manifests carried
`top_p=0.95` and `seed=20260730` for Anthropic runs. The provider sends neither
— the API rejects `top_p` alongside `temperature` and has no `seed` at all — and
Sonnet 5 and Opus 5 reject `temperature` outright with an HTTP 400. Caught by a
400 on a live call, which raised the question of what else in that row was
fiction.

**"The model never sees a salary" — a claim that was false for nine
milestones.** It appeared in the README twice and in `agents/gm.py` once, and no
longer appears anywhere as an assertion (see measurements entry 21). `CONTEXT_TEMPLATE` renders team payroll, apron
status, and `${salary:,}` for **every player on both rosters** — 29 money
strings in the first prompt of every arm. Caught by accident: Claude Haiku cited
"$55.7M" in a stand-pat reason, and checking whether it had recalled that or
been handed it produced the answer.

**Qwen's 31% and 65.5% in a per-scenario column.** Both figures are correct
*pooled across three arms*. Placed beside per-scenario hosted numbers they
asserted something the data never said. The real per-scenario figure for Qwen
unaided on the apron scenario is **0 of 12** — identical to Sonnet and Opus.
Caught by rebuilding the table from the recorded manifests.

**Five of the seven were caught by rebuilding the number from primary artifacts
— manifests, event logs, the transaction log — rather than re-reading the
summary that reported it.** The other two came from a live API error and a
model quoting something it should not have had. None was caught by review.

### Two species, two rules

**A check that certifies a different surface than the claim it is cited for.**
Six of the seven. The legality test asserted verdicts, not the null. The
boundary test asserted schemas and outputs, never prompt text. The canary
asserted throughput, not whether throughput meant anything on that transport.
Each check was correct and each was cited for something it did not cover.

> **Standing rule.** A claim about what the model *sees* needs a test on
> rendered prompts, not on types. More generally: no claim in the README
> without a test asserting the surface the claim is actually about.

**A figure valid at one scope quoted at another.** The seventh, and a different
failure — nothing was mismeasured. 31% pooled across three arms is right; the
same number in a per-scenario cell is a different assertion.

> **Standing rule.** A figure carries its scope. A pooled number may not occupy
> a per-scenario cell, and any table mixing the two states which is which.

### The catalogue has a selection bias

Ten of the eleven entries above **inflated** a result. That is not because
deflating errors are rare — it is because they survive. A number that says you
succeeded gets audited by anyone who doubts it; a number that says you failed
invites fixing the system rather than checking the metric. The pooled-null
error sat in the README as "precision is below chance" through several rounds
of work aimed at *raising* precision, and none of that work questioned the
denominator it was being measured against.

> **Standing rule.** A metric at or below its null gets the same audit as one
> that beats it. Failure is not self-certifying.

> **Extended to inputs.** An input that constrains the sim *away* from the
> observed outcome deserves the same scrutiny as one that points at it. Two
> confirmed deflating instances: the pooled null (#11) and a free-agent pool
> built from the answer's own table, which excluded every actual re-signee
> from the market and hid for three milestones (entries 43/45). An error that
> lowers a result survives because failure reads as a system problem rather
> than a measurement problem.


### The power rule

Null-before-metric has a sibling: **state the smallest effect a design can
detect before running it.** The 5-vs-30 comparison could not have separated the
configurations on any scored metric — 14 proposals against 2 actual arrivals in
a ~520-player pool expects ~0.05 hits by chance, so equal scores were near
certain *before the run started*, and that was computable in advance. The run
was still worth doing because composition (who signs whom) had the sample to
move, and it did: 4 of 5 scored teams changed baskets. Running a comparison
whose primary metric cannot move is only defensible when you say beforehand
which secondary observable can.

### What these produced

Each of these mechanisms exists because something got past the one before it:

- **Admissibility property tests for prunes** — a prune may over-admit, never
  under-admit. Added after a solver prune silently discarded 12 legal packages.
- **The throughput canary** — added after `gpu_fraction: 1.0` reported a healthy
  run through a 3x slowdown. It later fired mid-benchmark and aborted seven
  trials rather than record them.
- **Null before metric** — no number reported without what a do-nothing or
  random system scores on the same data. Now a charter rule.
- **Append-only runs with manifests** — every figure recomputable from primary
  artifacts, which is how five of the seven above were found.
- **Prompt-text assertions** — added after the salary claim, because every
  earlier boundary test constrained types.
- **A derived-facts registry, enumerated by AST** — every sim-side derivation
  from an outcome table declared with a direction and a freeze-computability
  answer, after `free_agent_pool()` hid for three milestones. It found two more
  leaks before any metric did; both repairs ran under predictions registered in
  their commits and both held. The same move as the writer tests and
  single-filter ratios — make the surface enumerable — applied to leakage.
- **A pre-commit hook running the suite** — added after a commit went in red and
  broke a milestone gate.
- **Merge-not-overwrite tests over every writer, enumerated programmatically** —
  added after three writers in one module were found to have the same defect,
  one at a time, a round apart each. Fixing one call site is not fixing the
  class, so a fourth writer is covered without anyone remembering to cover it.

**An aside that turned out to matter.** An eleven-season stats table and the
provenance for thirteen seasons were both destroyed by that defect and both
recovered from git — because data snapshots are committed. That was a
*reproducibility* decision, made so any figure could be recomputed from primary
artifacts. It turned out to be the backup.
