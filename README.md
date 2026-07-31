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

The model never sees a salary, never emits a package, never states terms.
Illegal proposals stopped being discouraged and became **unrepresentable**.

### Then it wanted players it could not afford: 0 of 7 intents satisfiable

Legal-by-construction is not useful if the intent is impossible. The fix was to
compute feasibility *before* asking. Three scenarios, three arms, 29–36 trials
each:

| | blind | feasible | unlock |
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

**Agents still never see a salary — including when you ask them about one.**
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

## Reading further

- [`docs/measurements.md`](docs/measurements.md) — **the measurement history**:
  every number, what it overturned, and what changed because of it. The most
  useful document here.
- [`docs/milestones.md`](docs/milestones.md) — the full build record, M0 onward.
- [`docs/backtests/lebron-2026.md`](docs/backtests/lebron-2026.md) — freeze,
  evidence, conditional commitments, cutoff enforcement.

702 tests, run by a pre-commit hook that exists because a commit once went in
red and broke a milestone gate.
