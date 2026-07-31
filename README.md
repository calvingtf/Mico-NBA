# MiroNBA

Multi-agent simulation of counterfactual NBA offseasons, built so the results
can be checked. Seed a decision that has not happened yet — *where does LeBron
James sign?* — simulate the league's reaction from a frozen world state, and
score the branch that actually occurred against what teams really did.

The point of the project is not the simulation. It is the harness around it:
deterministic rules an LLM cannot talk its way past, a freeze the simulator
cannot see through, and measurements that are allowed to come back negative.

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

## Run it

```bash
python -m mironba.eval.backtest      # the whole LeBron 2026 backtest, scored
```

Needs the data snapshots rebuilt first — see [Reproducing the data
snapshot](docs/milestones.md#reproducing-the-data-snapshot). Salary tables are
not redistributed; the ingest that fetches them is.

## What the measurements actually said

Every number below comes from a recorded run with a manifest. Several are
failures, and those are the useful ones.

**An LLM cannot do salary matching. 0 legal trade proposals in 12 attempts, and
9 repair retries rescued none.** So the architecture changed rather than the
prompt: the model states an *intent*, a deterministic solver enumerates legal
packages, the model picks an index. Illegal proposals became unrepresentable
rather than discouraged.

**Then it wanted players it could not afford — 0 of 7 intents satisfiable.** The
fix was to compute feasibility before asking. Across three scenarios, two arms:

| | blind | feasible |
| --- | --- | --- |
| Named an unreachable target | 65.5% | **0.0%** |
| Intent satisfiable, first attempt | 31.0% | **58.6%** |
| LLM calls spent | 102 | **99** |

**Naming which of your own contracts unlock a target closed the rest of the
gap** — 12/12 first-attempt satisfiable on the apron scenario, against 1/12.
The model then declined all twelve packages, which is a defensible read of a
thin market rather than a failure.

**The value model does not demonstrably beat its baseline, and says so.** Pooled
MAE 7.49 wins against 7.99 for "last season regressed to .500" — but paired over
120 team-seasons that is p=0.159, and dropping a single season collapses the
edge from 0.50 wins to 0.13.

**A win delta carries about 7.4 wins of error, measured.** Two earlier figures
(12.05 and 2.00) were both theoretical and disagreed about everything that
mattered. Against 180 real team-season transitions the answer sits close to the
pessimistic one, so options a few wins apart are **not** rankable — and the
comparison layer refuses to rank them.

**Multi-team backtest, scored on signings only:** recall 50% (1 of 2), precision
7.1%. The one hit is LeBron to Philadelphia, *which is the branch premise rather
than a prediction*.

| named metric | value |
| --- | --- |
| **Predictive recall on non-stipulated signings** | **0 of 1** |

That is the number to watch, and it is currently zero. The sim does get closer
than it did: correcting Philadelphia's freeze state to include Jaylen Brown's
$57.1M moved its LeBron offer from $40.9M of cap space to the $15,044,000
mid-level. But it does not reach the $3,876,529 minimum he actually took,
because at $181.1M Philadelphia was genuinely under the apron and the mid-level
was really available. Cap mechanics alone do not force that contract — the only
evidence for it is a statement dated after the freeze.

## What is honest about it

- **The freeze is enforced, not promised.** `world_state()` returns PRE-freeze
  items; POST items require an explicit unlock token, and a test greps the
  package to prove nothing outside `eval/` names it. A second test asserts no
  simulator module hardcodes a post-freeze figure — written after the scorer was
  caught doing exactly that.
- **The audit found a leak in our own ingest.** The transaction log runs three
  days past the freeze, and the offending row is a Golden State signing — the
  team whose behaviour was being predicted.
- **Bug fixes are proved to be fixes.** When centring the value model improved
  its score, a zero-sum invariant showed the defect was visible *in the training
  data* (1108 predicted wins for a league that plays 1230), so the change was a
  correction rather than a rescue.
- **Provenance or absence.** Every league constant carries a source and a
  confidence rating. Contract structure could not be sourced for historical
  seasons, so it is absent for them rather than recalled.

## Limitations, in order of how much they matter

1. **The GM planner in the backtest is deterministic, not an LLM.** The LLM path
   is measured separately, in the A/B tables above. Mixing them would make a
   failure unattributable — but it means the backtest scores the *rules*, not
   the model's judgement.
2. **Availability is ignored.** Games played is not games available. A roster
   model blind to injuries misprices teams, and roughly a third of the measured
   delta error is availability rather than model error.
3. **Ground truth cannot separate a signing from a trade** for unsourced
   arrivals. Nine of seventeen are labelled `unknown` rather than guessed.
4. **A counterfactual branch has no ground truth and never will.** Reported as
   unfalsifiable and not scored.
5. **Contested resolution is thin.** Roster tier, offer, and reported
   commitments. There is no player-agency model, which is why the simulation
   puts LeBron on Philadelphia's mid-level rather than the minimum he took.
6. **One model, one quantization, one machine.** Throughput and latency figures
   describe an RTX 3090 running `qwen3.6:27b` at Q4_K_M.

## Reading further

- [`docs/measurements.md`](docs/measurements.md) — the measurement history: what
  each number was, and what changed because of it.
- [`docs/milestones.md`](docs/milestones.md) — the full record, M0 through M5.
- [`docs/backtests/lebron-2026.md`](docs/backtests/lebron-2026.md) — the
  backtest: freeze, evidence, conditional commitments, cutoff enforcement.
- `CLAUDE.md` — the project charter.

Tests: `python -m pytest`.
