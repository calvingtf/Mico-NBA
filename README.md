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

| | blind | feasible | unlock |
| --- | --- | --- | --- |
| Named an unreachable target | 65.5% | **0.0%** | **0.0%** |
| Intent satisfiable, first attempt | 31.0% | 58.6% | **100%** |
| Intent satisfiable, final | 58.6% | 75.9% | **100%** |
| LLM calls spent | 102 | 99 | **75** |

**Naming which of the team's own contracts unlock each target closed the gap
completely** — 23 of 23 intents satisfiable on the first attempt, at a quarter
fewer calls than the baseline, because no revision round was ever needed.

Stand-pat rates are 19.4%, 19.4% and 20.7% across the three arms, so the
improvement is not a selection effect from the model simply attempting less.

The model then **declined 16 of the 23 legal package sets it was shown**. On an
apron team whose only legal moves are cheap-for-cheap swaps that is a
defensible read of a thin market rather than a failure — but it means solving
feasibility did not produce trades, it produced informed refusals.

*Caveat: the unlock arm ran 29 trials to the others' 36. The last cell aborted
part-way when the throughput canary fired at 67% below baseline — see below.*

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
| **Validator legality on real trades** | **5 of 5** (100%), n=5 |

The second caps everything downstream: a validator that rejects real trades
would make every simulated market wrong in the same direction. It was 4 of 6
before service years were sourced and draft-rights trades were separated out
from contract trades. But the denominator is the story — of 33 two-team trades
with players on both sides across three seasons, only **5 can be priced at
all**. The rest hit players with no salary row (20) or were trades of draft
rights rather than contracts (8). 100% of what it can see, and it can see 15%.

That is the number to watch, and it is currently zero. The sim does get closer
than it did: correcting Philadelphia's freeze state to include Jaylen Brown's
$57.1M moved its LeBron offer from $40.9M of cap space to the $15,044,000
mid-level. But it does not reach the $3,876,529 minimum he actually took,
because at $181.1M Philadelphia was genuinely under the apron and the mid-level
was really available. Cap mechanics alone do not force that contract — the only
evidence for it is a statement dated after the freeze.

**The deadline backtest still predicts nothing, and two of the three reasons
turned out to be fixable.** All three deadlines, each frozen on the day:

| | 2023-24 | 2024-25 | 2025-26 | pooled |
| --- | --- | --- | --- | --- |
| proposed | 213 | 208 | 0 | **421** |
| real two-team trades | 0 | 1 | 3 | **4** |
| counterparty pairs matched | 0 | 1 | 0 | **1** |
| solver legality on real trades | — | 1/1 | 3/3 | **4/4** |

1. **The disposition gate was wrong, and two green tests said it was right.**
   It thresholded games back with the *value model's* 10.5-win error bar. That
   is uncertainty on a counterfactual roster delta; games back on the freeze
   date is a completed fact. It sent 23 of 30 teams to `AMBIGUOUS`, and since
   ambiguous teams acted on neither side, the sim missed every deal between
   middling teams. Measured properly over 90 team-seasons — 37/37 of teams 4+
   games clear of the cut made the top ten, 18/18 of teams 3+ back did not —
   the bands are 3.0 and 4.0, giving 12 buyers, 6 sellers, 12 ambiguous. The
   ambiguous teams now act.
2. **The planner now has a notion of value**, taken from the *previous
   completed* season, which is fully pre-freeze by construction and leaks
   nothing. Ordering targets by cost had produced Jayson Tatum and Payton
   Pritchard for Zion Williamson. Value alone was not enough: it constrains
   only the acquiring side, and the first run with it sent Stephen Curry and
   Joel Embiid to Atlanta on the same tick. A supplier-side gate — a seller
   parts with anyone, a team still in the race parts only with a below-median
   player — cut proposals from 380 to 208 and took the absurdities with them.
3. **The two-team restriction is not fixable and is now a stated limit.** A
   coverage pass found the denominator is *not* lost to missing salaries, as
   expected: nothing in-window was dropped for pricing. It is lost because real
   deadline business is multi-team. Of 19 trade rows in the 2025 window, 13
   carry no players at all (picks, cash and trade exceptions) and 5 involve
   three or more teams. Pooled across three deadlines, **4** real trades are
   scoreable. That is not a sample, so no precision claim is made from it.

**What the numbers say now:** the solver is 4 of 4 on the legality of real
trades, and the planner's precision is 1 in 421. It enumerates legal
permutations; it does not model a market. Two of three named causes are gone
and the headline did not move — which is the useful result, and the reason
the limitation above is stated in proposals rather than in matches.

## What is honest about it

- **The freeze is enforced, not promised.** `world_state()` returns PRE-freeze
  items; POST items require an explicit unlock token, and a test greps the
  package to prove nothing outside `eval/` names it. A second test asserts no
  simulator module hardcodes a post-freeze figure — written after the scorer was
  caught doing exactly that.
- **The canary stops a degraded run rather than reporting it.** It fired for
  the first time mid-benchmark, at 11.72 tok/s against a 36.02 baseline, and
  aborted the remaining trials. That is the same memory-pressure failure that
  silently corrupted an earlier milestone's latency column while
  `gpu_fraction: 1.0` said everything was fine.
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

1. **The deadline planner has terrible precision: 421 proposals, 1 match.** It
   enumerates legal permutations rather than modelling a market. Value gating
   both sides cut it roughly in half and removed the absurdities (it had been
   proposing Curry and Embiid to Atlanta on the same tick), but two orders of
   magnitude too many is the honest headline, not a rounding error.
2. **Trades are two-team only, and real deadline business is not.** On
   2025-02-06 there were 13 trades and not one was a two-team deal with players
   moving both ways. That leaves a pooled denominator of **4** real trades
   across three deadlines, so no precision claim is made from it and recall is
   reported as a count. Multi-team trades and draft-pick valuation are the two
   named things this project does not do; the denominator is the direct cost.
3. **The GM planner in the backtest is deterministic, not an LLM.** The LLM path
   is measured separately, in the A/B tables above. Mixing them would make a
   failure unattributable — but it means the backtest scores the *rules*, not
   the model's judgement.
4. **Availability is ignored.** Games played is not games available. A roster
   model blind to injuries misprices teams, and roughly a third of the measured
   delta error is availability rather than model error.
5. **Ground truth cannot separate a signing from a trade** for unsourced
   arrivals. Nine of seventeen are labelled `unknown` rather than guessed.
6. **A counterfactual branch has no ground truth and never will.** Reported as
   unfalsifiable and not scored.
7. **Contested resolution is thin.** Roster tier, offer, and reported
   commitments. There is no player-agency model, which is why the simulation
   puts LeBron on Philadelphia's mid-level rather than the minimum he took.
8. **One model, one quantization, one machine.** Throughput and latency figures
   describe an RTX 3090 running `qwen3.6:27b` at Q4_K_M.

## Reading further

- [`docs/measurements.md`](docs/measurements.md) — the measurement history: what
  each number was, and what changed because of it.
- [`docs/milestones.md`](docs/milestones.md) — the full record, M0 through M5.
- [`docs/backtests/lebron-2026.md`](docs/backtests/lebron-2026.md) — the
  backtest: freeze, evidence, conditional commitments, cutoff enforcement.
- `CLAUDE.md` — the project charter.

Tests: `python -m pytest`.
