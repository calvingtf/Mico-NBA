# MiroNBA

A multi-agent simulation of counterfactual NBA offseasons, built so its results
can be disproved. Seed a decision that has not happened yet — *where does LeBron
James sign?* — simulate the league's reaction from a frozen world state, and
score the branch that actually occurred against what teams really did.

**Most of what follows are negative results.** They are the point. The
simulation is ordinary; the harness around it is not, because it is built to
report failure at the resolution needed to act on it.

---

## What was measured

### An LLM cannot do salary matching: 0 legal trades in 12 attempts

Propose-validate-retry, a capable local model, the cap rules in the prompt.
Twelve proposals, zero legal, and nine repair retries rescued **none** of them.
Salary matching is a constraint-satisfaction problem over integer contracts and
the model was reliably off by millions.

So the architecture changed rather than the prompt:

```
model states an INTENT  ->  solver enumerates every LEGAL package  ->  model picks an INDEX
```

The model **does** see team payroll, apron status, and every contract on both
rosters — that is what `CONTEXT_TEMPLATE` renders. What it cannot do is **emit a
package or state terms**: it names a player, an intent, or an index, and the
solver builds the deal. Illegal proposals stopped being discouraged and became
**unrepresentable**.

*(The first sentence used to read "the model never sees a salary". That was
false for four milestones and no test covered it — see measurements entry 21.)*

### Then it wanted players it could not afford: 0 of 7 intents satisfiable

Legal-by-construction is not useful if the intent is impossible. The fix was to
compute feasibility *before* asking. Three scenarios, three arms, 29–36 trials
each:

**All figures below are POOLED across three scenarios.** They may not be placed
beside per-scenario numbers — doing exactly that produced the seventh error
above. The arm formerly called `blind` is now `unaided`: it meant "without the
feasible-target list", never "without money".

| *(pooled, 3 scenarios)* | unaided | feasible | unlock |
| --- | --- | --- | --- |
| Named an unreachable target | 65.5% | **0.0%** | **0.0%** |
| Intent satisfiable, first attempt | 31.0% | 58.6% | **100%** |
| Intent satisfiable, final | 58.6% | 75.9% | **100%** |
| LLM calls spent | 102 | 99 | **75** |

Telling the model *which of its own contracts unlock each target* closed the gap
completely — 23 of 23 on the first attempt, at **a quarter fewer calls**,
because no revision round was ever needed. Stand-pat rates are 19.4 / 19.4 /
20.7%, so this is not the model simply attempting less.

The model then **declined 16 of the 23 legal package sets it was shown**. On an
apron team whose only legal moves are cheap-for-cheap swaps that is a defensible
read of a thin market — but solving feasibility produced *informed refusals*,
not trades.


### Model comparison: where constraints bind, only the scaffolding works

Qwen 27B against three hosted models, `unaided` arm, **per scenario** — not
pooled, for the reason given above. 12 trials per cell unless noted.

**Los Angeles — above the first apron, constraints bind:**

| model | arm | acted | 1st attempt | final |
| --- | --- | --- | --- | --- |
| Qwen 27B | unaided | 12/12 | **0/12** | **0/12** |
| Sonnet 5 | unaided | 12/12 | **0/12** | **0/12** |
| Opus 5 | unaided | 12/12 | **0/12** | **0/12** |
| **Qwen 27B** | **unlock** | **21/21** | **21/21** | **21/21** |

**No model succeeds unaided, at any capability.** A frontier model is not better
than the 27B here — all three are at zero. The scaffolding takes the *weakest*
model to 21/21.

**Chicago (over the cap, clear of the aprons) and the positive control (under
the cap, ten legal packages verified before any model saw it):**

| model | Chicago 1st / final | control 1st / final |
| --- | --- | --- |
| Qwen 27B | 5/12 → 12/12 | 5/14 → 8/14 |
| Haiku 4.5 | 0/12 acted at all | 2/12 → 8/12 |
| Sonnet 5 | 11/13 → 12/13 | **0/12 → 12/12** |
| Opus 5 | 9/24 → 23/24 | **12/12 → 12/12** |

Capability does substitute where constraints do not bind: Opus is 12/12 on the
first attempt at the control where Qwen manages 5 of 14. **Sonnet's control cell
is the one place the retry does all the work** — 0/12 first attempt, 12/12
final, meaning every intent it formed was initially unsatisfiable and every one
was rescued by a single revision round.

The control exists because the other scenarios cannot separate good judgment
from refusal: declining a bad trade is correct and most trades are bad. Haiku
standing pat 24/24 elsewhere was unreadable until it acted 11/12 here — it was
judging, not refusing.

**These numbers are not sampling-matched.** `temperature` is deprecated on
Sonnet 5 and Opus 5 (HTTP 400), no seed is available on any of them, and their
manifests are flagged `NOT REPRODUCIBLE` with the reason recorded. The Qwen arms
ran at temperature 0.8 with a fixed seed. The comparison supports a claim about
*which arm wins*, not a precise ranking between models.

### The value model is directionally useful and not demonstrably better than regression to the mean

Pooled MAE **7.49 wins** against **7.99** for "last season, regressed to .500".
Paired over 120 team-seasons that is **p=0.159**, and dropping a single season
collapses the edge from 0.50 wins to 0.13. It is kept, and it is reported as not
beating the baseline.

### A win delta carries about 10.5 wins of error, measured

Two earlier figures — 12.05 and 2.00 — were both theoretical and disagreed about
everything that mattered. Measured against **180 real team-season transitions**:

| | sd | MAE | |
| --- | --- | --- | --- |
| all transitions | **10.48** | 8.20 | r = +0.49 |
| low-disruption subset | 7.40 | 7.03 | n=30 |

An earlier claim that options 3 wins apart were rankable was **withdrawn** — the
pessimistic of the two theories was nearly right. The comparison layer now
refuses to rank inside its own error bar: two projections must differ by
**10.5 wins** to be called apart, which is `z·sd·√2` on the *favourable* 7.40
subset. Three options at 44.2, 42.9 and 41.4 come back as one tier.

### The deadline planner does not beat a random proposer

The only place the trade solver is scored *predictively*, and the result is
negative in a way that took a null baseline to see. All three seasons now have
standings coverage, so the planner runs everywhere:

| | 2023-24 | 2024-25 | 2025-26 | pooled |
| --- | --- | --- | --- | --- |
| proposed | 213 | 208 | 252 | **673** |
| distinct pairs covered (of 435) | 212 | 206 | 244 | — |
| % of the pair space | 48.7% | 47.4% | 56.1% | — |
| real trades in window | 2 | 3 | 8 | **13** |
| matched | 2 | 3 | 6 | **11** |
| recall | 100% | 100% | 75% | **85%** |

**Read the third row before the last one.** The proposals cover about half of
all 435 team pairs, and a two-team proposal counts against a three-team trade,
so each real trade has three chances to be hit:

| | observed | null | |
| --- | --- | --- | --- |
| counterparty matches | 11 of 13 | **10.18 expected** | P(null ≥ observed) = **0.426** |
| precision | **2.97%** | **6.67%** random proposer | below chance |

**Precision is below what a random proposer scores.** The counterparty metric
is indistinguishable from chance. Both are labelled as measures of proposal
volume, not of identifying who trades with whom.

Two named causes were fixed before this — the disposition gate and the absence
of any player value — and precision did not move. A third refinement moved
proposals 421 → 415 and precision not at all.

**The planner enumerates legal permutations; it does not model a market.**

### The validator's legality rate is a false-rejection rate

The README used to say "5 of 5 on real trades". Every real trade in that set is
legal — the league approved them — so **a validator that approves everything
scores 100%**, and that is the null. Nothing here can reward catching an
illegal trade, because the set contains none.

| verdict on real trades | n |
| --- | --- |
| APPROVED | **0** |
| UNDETERMINED | 7 |
| REJECTED | 3 |

Ours scores **70%** against a 100% null: a **30% false-rejection rate**. And the
70% counts `UNDETERMINED` as legal — with those excluded it is **0 of 3**, because
no real trade is ever approved outright. Every non-rejection is an undetermined,
usually base-year compensation needing a re-sign status the ingest lacks.

That the validator *rejects* illegal trades is shown by the M0 synthetic
coverage matrix, which contains illegal ones. The two are no longer conflated.

---

---

## Seven results that weren't

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

## See it

Three commands, each reading a completed run. None of them changes a result.

```bash
python -m mironba.report.timeline runs/<run-id>     # the event log as a feed
python -m mironba.agents.report   runs/<run-id>     # prose, with claims filtered
python -m mironba.agents.chat     runs/<run-id> "why did you decline?"
python -m mironba.report.html     --out docs/example-run.html
```

The last produces **[`docs/example-run.html`](docs/example-run.html)** — one
self-contained file, no server, no external requests. It is committed, so it
can be opened straight from the repo.

**The feed leads with refusals, because refusal is what this system measurably
does.** A run that declined everything:

```
   08:26:49    5  [LAL] LAL wants 1 target(s): Gary Payton
          "Curry is unavailable per engine constraints. Gary Payton II offe"
   08:26:49    6  [solver] solver: 0 legal package(s), binding: ROSTER_LIMIT
** 08:26:49    7  [LAL] REFUSED by the rules - no legal package exists
** 08:29:22   11  [LAL] LAL DECLINES every package it was shown
          "The proposed trade swaps Dalton Knecht for Brandin Podziemski. W"

  2 refusal/failure event(s) of 10 rendered
  verdict=None  stood_pat=False  declined_all=True  first_intent_satisfiable=False
```

Every line traces to one event in `events.jsonl` by sequence number. Nothing is
inferred and no model is involved in the rendering — full example in
[`docs/example-timeline.txt`](docs/example-timeline.txt).

**The report agent cannot oversell, structurally.** The numbers and the
limitation block are module constants appended after the model's prose, so no
prompt failure can drop them, and a filter removes any sentence that presents a
simulated outcome as a prediction or ranks options the value model cannot
separate. Dropped sentences are *shown*, not silently discarded. Six tests in
`tests/test_surface.py` assert every limitation reaches both the terminal
report and the HTML.

**Agents cannot state terms — including when you ask them about money.**
Money questions are routed to the solver's own record rather than to the model
([`docs/example-chat.txt`](docs/example-chat.txt)):

```
$ ... chat <run> "how much cap room did you have?"

  [solver record, not the model]
  The agent never saw a salary, so this comes from the solver's record for
  this run: the solver's absorbable ceiling was $154,856,190; ... Closest
  attempt: sending Dalton Knecht ($3,819,120) allows $7,888,240 back,
  $1,241,760 short of $9,130,000.
```

Ask it a reasoning question instead and it answers from the option set it was
actually shown, which is in the run — so "why did you decline" has a referent
rather than a confabulation.

---

## How it is put together

```
                  ┌────────────────────────────────────────┐
  data/           │ Basketball-Reference  ->  contracts    │ scraped, not redistributed
                  │ stats.nba.com         ->  box scores   │ league API, committed
                  └───────────────┬────────────────────────┘
                                  v
  rules/    cap . trade_validator . solver . signing . signing_solver
            -- deterministic, unit-tested. Enumerates every LEGAL option.
                                  |
                  ┌───────────────┴────────────────┐
                  v                                v
  agents/   GM: names a player, an intent,    models/  value -> win_delta
            or an index. Never a salary,               -> delta_error -> compare
            never a package, never terms.              -- refuses to rank
                  |                                       inside its own error
                  └───────────────┬────────────────┘
                                  v
  world/    manifest . events . evidence (PRE|POST freeze) . pending decisions
                                  v
  sim/      tick . branch . league (contention + event-driven scheduler)
                                  v
  eval/     the only code allowed to read the answer
```

## What is honest about it

- **The freeze is enforced, not promised.** `world_state()` returns PRE-freeze
  items; POST items require an explicit unlock token, and a test greps the
  package to prove nothing outside `eval/` names it. A second test asserts no
  simulator module hardcodes a post-freeze figure — written after the scorer was
  caught doing exactly that.
- **The canary stops a degraded run rather than reporting it.** It fired for the
  first time mid-benchmark, at 11.72 tok/s against a 36.02 baseline, and aborted
  the remaining trials — costing a cell of the A/B, which is stated wherever
  that cell is quoted. It is the same memory-pressure failure that silently
  corrupted an earlier milestone's latency column while `gpu_fraction: 1.0` said
  everything was fine.
- **The audit found a leak in our own ingest.** The transaction log ran three
  days past the freeze, and the offending row was a Golden State signing — the
  team whose behaviour was being predicted.
- **Bug fixes are proved to be fixes.** When centring the value model improved
  its score, a zero-sum invariant showed the defect was visible *in the training
  data* (1108 predicted wins for a league that plays 1230), so the change was a
  correction and not a rescue.
- **A figure in the brief was checked and corrected.** The cash limit was given
  as one number; the sourced 2024-25 figure was another, and the source won.
- **No metric is reported without its null.** A charter rule, added after three
  headline numbers turned out to be artifacts of how they were computed: a 200%
  recall, a legality rate that counted UNDETERMINED as legal, and a counterparty
  match that a random proposer beats. `docs/measurements.md` entry 20 audits
  every number in this README against it; three of nine fail and are relabelled.
- **Provenance or absence.** Every league constant carries a source and a
  confidence rating. Contract structure could not be sourced for historical
  seasons, so it is absent for them rather than recalled.

## Limitations, in order of how much they cost

1. **There is no market model, and the planner does not beat a random one.**
   It proposes 421 legal permutations covering half the 435-pair space, and its
   precision (2.14%) is *below* a random proposer's (2.99%). Fixing it needs
   value resolution finer than the evaluation layer has — the measured delta
   error is 10.48 wins and deadline trades turn on far less. Measured, not
   assumed: two named causes were fixed, a third refinement tried, and
   precision did not move.
2. **The scoreable denominator is 13, and 8 of those are unreachable.** Adding
   three-team trades doubled it (5 → 10 validated, 4 → 13 in deadline windows),
   but 2025-26 has no game-log ingest, so the planner never ran there. Recall
   is always reported split by whether the planner could run at all. Extending
   the ingest backwards is blocked on a Basketball-Reference rate limit, not on
   anything in this repo.
3. **Draft picks are not valued.** Every pick is worth zero, which makes the most
   common deadline currency invisible. Most trade rows in a deadline window carry
   no players at all — picks, cash and trade exceptions.
4. **Trade coverage is thin and the validator errs on what it sees.** Three-team
   parsing doubled the scoreable set to 10; the rest still hit players with no
   salary row or moved draft rights rather than contracts. On those 10 the
   validator rejects 3 that the league approved — a 30% false-rejection rate
   against a 100% approve-everything null.
5. **The GM planner in the backtest is deterministic, not an LLM.** The LLM path
   is measured separately, in the A/B above. Mixing them would make a failure
   unattributable — but the backtest scores the *rules*, not the model.
6. **Availability is ignored.** Games played is not games available; roughly a
   third of the measured delta error is availability rather than model error.
7. **A counterfactual branch has no ground truth and never will.** Reported as
   unfalsifiable and not scored.
8. **One model, one quantization, one machine.** Throughput and latency describe
   an RTX 3090 running `qwen3.6:27b` at Q4_K_M.

## What is still unmeasured

Named because an absent measurement that nobody names reads as a measurement
that came back fine.

- **Whether the persona does anything.** Citation of persona parameters in
  stated reasons falls monotonically with capability — Qwen 78.3%, Sonnet 67.7%,
  Haiku 61.2%, Opus 50.6% — and Qwen restates the numbers *as* its justification
  where stronger models reason about cap state. That is consistent with the
  persona being text the weak model recites rather than a disposition it acts
  from. **The permutation control that would settle it has not been run**:
  permute the parameters, hold everything else, measure whether behaviour moves.
  Until then every persona-driven result here carries this caveat.
- **The salary-free arm.** A fourth arm with all money stripped from the
  rendered context, to ask what the numbers were doing in the model's reasoning
  now that we know it can see them. Free to run on the local model. Not run.
- **Re-scoring across the ten ingested seasons.** Seven seasons were backfilled
  and the CBA-era machinery is in place, but legality has not been re-scored at
  scale, the 2017-vs-2023 era split has not been computed, and the deadline
  backtest has not been re-run with the pair null recomputed at the larger
  denominator. Entirely deterministic; no model calls needed.
- **Whether the LLM plans a better offseason than the deterministic planner.**
  The backtest uses the deterministic one on purpose, so a failure is
  attributable. The comparison has not been run.
- **The counterfactual branch.** Unfalsifiable by construction. Run and
  reported, never scored.

## Reading further

- [`docs/measurements.md`](docs/measurements.md) — **the measurement history**:
  every number, what it overturned, and what changed because of it. The most
  useful document here.
- [`docs/milestones.md`](docs/milestones.md) — the full build record, M0 onward.
- [`docs/backtests/lebron-2026.md`](docs/backtests/lebron-2026.md) — freeze,
  evidence, conditional commitments, cutoff enforcement.

702 tests, run by a pre-commit hook that exists because a commit once went in
red and broke a milestone gate.
