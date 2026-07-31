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

### The deadline planner's precision is 1 in 421

The only place the trade solver is scored *predictively*. Three deadlines, each
frozen on the day:

| | 2023-24 | 2024-25 | 2025-26 | pooled |
| --- | --- | --- | --- | --- |
| proposed | 213 | 208 | 0 | **421** |
| real two-team trades | 0 | 1 | 3 | **4** |
| counterparty pairs matched | 0 | 1 | 0 | **1** |
| solver legality on real trades | — | 1/1 | 3/3 | **4/4** |

Three causes were named. **Two were fixed and the headline did not move**:

1. *The disposition gate was wrong, and two green tests had pinned it there.*
   It thresholded games back with the value model's win-delta error — a
   projection's spread applied to a completed fact. 23 of 30 teams came back
   `AMBIGUOUS` and acted on neither side, so the sim missed every deal between
   middling teams. Measured over 90 team-seasons (37/37 of teams 4+ games clear
   made the top ten; 18/18 of teams 3+ back did not), the bands are 3.0 and 4.0,
   giving 12 buyers / 6 sellers / 12 ambiguous. Ambiguous teams now act.
2. *The planner had no notion of value.* It ordered targets by cost and proposed
   Jayson Tatum and Payton Pritchard for Zion Williamson. It now uses the
   **previous completed season**, pre-freeze by construction. That alone was not
   enough — value gates only the acquiring side and is near zero-sum, so the
   first run with it sent Curry *and* Embiid to Atlanta on the same tick. A
   supplier-side gate cut proposals 380 → 208 and took the absurdities with it.
3. *The two-team restriction is not fixable here*, and is a stated limit below.

Applying that gate symmetrically — the proposing team's outgoing package must
also survive its own disposition — moved proposals 421 → 415 and precision not
at all. Left off by default, and recorded as measured rather than assumed.

**The planner enumerates legal permutations; it does not model a market.** Two
of three causes are gone and it is still two orders of magnitude too eager.
That is the honest headline.

---

## Run it

```bash
python -m mironba.eval.backtest      # the whole LeBron 2026 backtest, scored
```

One command, no arguments. Recorded output, so you never have to run it:

```
  BRANCHES

  signs_elsewhere  [ACTUAL]
    GSW  Kentavious Caldwell-Pope, Seth Curry, DeMar DeRozan
    MIA  Keshad Johnson, Maxi Kleber, Jonas Valančiūnas, Jahmir Young
    PHI  LeBron James, Emoni Bates, Kyle Lowry, Cameron Payne, Anfernee Simons
    8 contested, 0 resolved arbitrarily
    scheduler: 17 wakes vs 65 polled (74% saved)

  signs_with_blocker  [COUNTERFACTUAL]
    GSW  LeBron James, Kentavious Caldwell-Pope, Seth Curry
    PHI  Emoni Bates, Kyle Lowry, Cameron Payne, Anfernee Simons
    8 contested, 1 resolved arbitrarily

  SCORED - signs_elsewhere only. The counterfactual has no ground
  truth and never will, so it is not scored.

  signings only (headline)     recall  50.0%   precision   7.1%   (hits 1 of 2)
  all post-freeze arrivals     recall  18.2%   precision  14.3%   (hits 2 of 11)
  contested-player accuracy    0 of 1 resolvable (0%)

  LeBron's destination is the branch premise, not a prediction.
  Predictive recall on non-stipulated signings is 0 of 1.

  LEAKAGE AUDIT
  OK  evidence file (PRE partition)     16 checked  20 POST items withheld
  OK  conditional commitments            4 checked  2 POST commitments withheld
  OK  transaction log (2025-26)       1131 checked  2 post-freeze row(s) excluded
  OK  player performance ingest         11 checked  seasons 2014-15..2024-25
  OK  contract snapshot (GSW roster)    12 checked  5 post-freeze signing(s) excluded
```

Read the last block first. **Predictive recall on non-stipulated signings is 0
of 1** — the one headline hit is LeBron to Philadelphia, which is the branch
*premise*, not a prediction. The simulation does get closer than it did:
correcting Philadelphia's freeze state to include Jaylen Brown's $57.1M moved
its offer from $40.9M of cap space to the $15,044,000 mid-level. It does not
reach the $3,876,529 minimum he actually took, because at $181.1M Philadelphia
was genuinely under the apron and the mid-level really was available. Cap
mechanics alone do not force that contract.

Needs the data snapshots rebuilt first — see [Reproducing the data
snapshot](docs/milestones.md#reproducing-the-data-snapshot). Salary tables are
not redistributed; the ingest that fetches them is.

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
- **Provenance or absence.** Every league constant carries a source and a
  confidence rating. Contract structure could not be sourced for historical
  seasons, so it is absent for them rather than recalled.

## Limitations, in order of how much they cost

1. **There is no market model.** The planner enumerates legal permutations and
   proposes 421 of them where roughly fifteen trades happened. Fixing it needs
   value resolution finer than the evaluation layer has — the measured delta
   error is 10.48 wins and deadline trades turn on differences far smaller than
   that. This is measured, not assumed: two named causes were fixed and
   precision did not move.
2. **Trades are two-team only, and real deadline business is not.** On
   2025-02-06 there were 13 trades and *not one* was a two-team deal with players
   moving both ways. Pooled across three deadlines that leaves **4** scoreable
   trades, so no precision claim is made from that denominator and recall is
   reported as a count.
3. **Draft picks are not valued.** Every pick is worth zero, which makes the most
   common deadline currency invisible. Most trade rows in a deadline window carry
   no players at all — picks, cash and trade exceptions.
4. **Trade coverage is ~15%.** Of 33 two-team trades with players on both sides
   across three seasons, only **5 can be priced at all**; the rest hit players
   with no salary row (20) or moved draft rights rather than contracts (8). The
   validator is 5 of 5 on what it can see, and it can see 15%.
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

673 tests, run by a pre-commit hook that exists because a commit once went in
red and broke a milestone gate.
