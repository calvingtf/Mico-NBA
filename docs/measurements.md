# The measurement history

Every number that changed the design, what it was, and what changed because of
it. Ordered as they happened, because several of them only make sense as
corrections to the one before.

The through-line: **each measurement was set up so it could come back negative,
and several did.** The ones that did are the reason the architecture looks the
way it does.

---

## 1. The propose-validate loop: 0 of 12

**Measured.** A GM agent proposed a trade; `rules/` judged it. Twelve live
attempts on a real roster produced **zero legal proposals**. Nine repair
retries — the validator's rejection fed back with the error — rescued **none**.

**What it meant.** Salary matching is integer constraint satisfaction over a
subset-selection space. The model was not typing badly; it was being asked to
solve a problem language models do not solve. Every retry produced a differently
illegal package.

**What changed.** Not the prompt. The architecture: the model states a
*TradeIntent* (who it wants, who it will give up — no salaries, no pairings), a
deterministic solver enumerates the legal packages, and the model picks an
index. `TradeProposal` was deleted from the schema module, and a test asserts no
agent-facing schema can pair an outgoing player with an incoming one.

**The honest footnote.** That 0/12 was measured against a validator that was
itself too strict — `_self_consistent_tier` skipped `OVER_CAP`, so most of the
league got flat apron matching instead of the bracket table. The redesign still
stands, but the number was worse than the rules alone warranted.

---

## 2. Legal-proposal rate became meaningless, so it was dropped

After the redesign the legal-proposal rate is **100% by construction** — the
solver cannot emit an illegal package. Reporting it would have been
self-congratulation. It was replaced with *intent satisfiability*: how often
what the model wants is achievable at all.

Which immediately produced the next negative result.

---

## 3. Intent satisfiability: 0 of 7

**Measured.** The model no longer proposed illegal trades. It wanted players it
could not afford. Every intent was blocked on `SALARY_MATCH`, and the solver was
right every time — Detroit was "under the cap" by $207,451 and the model offered
$13.0M for a $42.2M player.

**What changed.** The same move, one step earlier: compute what is *possible*
before asking what is wanted. The solver pre-filters the league to players the
team could legally acquire and hands the model that list — names only, never
prices.

---

## 4. The three-arm A/B

Three prompts, same solver, same seeds, both arms kept permanently in the
harness so the delta is attributable.

| across 29 intents | blind | feasible | delta |
| --- | --- | --- | --- |
| Named an unreachable target | 65.5% | **0.0%** | −65.5pt |
| Satisfiable, first attempt | 31.0% | **58.6%** | +27.6pt |
| Satisfiable, final | 58.6% | **75.9%** | +17.2pt |
| LLM calls spent | 102 | **99** | −3 |

The intervention cost *fewer* calls than the baseline, because it needed fewer
repair rounds.

**Keeping the old arm earned itself immediately.** Detroit measured 80%
first-attempt satisfiability in the blind arm, against 0% in the earlier
milestone — and almost none of that was the intervention. It was the model
switch and a prune bug fix. Reusing the old number as a baseline would have
credited the new prompt with a swing it did not produce.

**Third arm.** Naming *which* of the team's own contracts unlock each target
took the apron scenario to **12/12** first-attempt satisfiable, from 1/12. The
model then declined all twelve legal packages — which for a contender offered
only cheap-for-cheap swaps is a defensible read rather than a failure.

---

## 5. A prune that silently deleted legal trades

**Found by disagreement.** The pre-filter reported that the Lakers could acquire
nobody. Brute force over every subset, with no bound at all, found **twelve
legal packages**.

**The cause.** The search prune called `max_incoming_salary` with no post-trade
tier, which answers a deliberately conservative question. Golden State at $1.6M
under the first apron gets $8,000,000 from it, where the true ceiling is
$9,591,056 — enough to land one dollar below the line.

**Why it mattered.** Nothing runs behind a prune. A bound tighter than the
validator's own deletes legal options with no error and no log line. "The Lakers
can acquire nobody" read as a finding about an inflexible roster; it was an
artifact of the search.

**What changed.** `matching_upper_bound` takes the maximum over every tier, so a
prune may over-admit and pay in wasted validations but can never under-admit.
Tested against brute force on real payrolls, because the synthetic fixtures
never sat near an apron — which is the only place the two answers differ.

---

## 6. The value model that did not beat its baseline

**Measured.** Fit on earlier seasons, predict a held-out season's win totals,
against two baselines.

| held-out | v0 | previous-season wins | regressed to .500 |
| --- | --- | --- | --- |
| 2021-22 | **7.46** | 8.17 | 7.87 |
| 2022-23 | **6.40** | 8.07 | 6.55 |
| 2023-24 | **7.50** | 8.47 | 9.13 |
| 2024-25 | 8.58 | 9.13 | **8.41** |
| **pooled** | **7.49** | 8.46 | 7.99 |

Lower MAE in three of four seasons and pooled. The first draft of this document
said "beats both baselines".

**Then it was tested properly.** Paired over 120 team-seasons:

| comparison | mean difference | p |
| --- | --- | --- |
| vs previous-season wins | −0.97 wins | **0.053** |
| vs regressed to .500 | −0.50 wins | **0.159** |

Neither clears the threshold. And dropping 2023-24 alone collapses the edge over
the regressed baseline from −0.50 to **−0.13** wins.

**What changed.** The claim. It now reads *at least as good as the baselines and
probably a little better, not demonstrably better* — and the reason to stop
building is that the evidence justifies more seasons, not more model.

---

## 7. The zero-sum invariant: proving a fix was not a rescue

**The problem with the fix.** The value model was changed *after* seeing it lose,
which is exactly when a "fix" deserves suspicion. Centring team strength within
season improved held-out MAE from 9.43 to 7.50. Convenient.

**The test that settles it.** Thirty teams share exactly 1,230 wins every
season, so a win model must reproduce that total for each *training* season —
no held-out data, no baseline, no comparison. The uncentered model does not:

| training season | mean strength | predicted wins | actual | error/team |
| --- | --- | --- | --- | --- |
| 2015-16 | −0.719 | 1108.7 | 1230 | **−4.04** |
| 2018-19 | −0.071 | 1268.1 | 1230 | +1.27 |
| 2022-23 | +0.541 | 1419.0 | 1230 | **+6.30** |

Predicting 1,108 wins for a league that plays 1,230 is wrong on its own terms.
The drift is an era effect — the metric's largest weight is on made threes, and
three-point volume grew — and it runs parallel to the +7.81 held-out bias.

**Why an ordinary fit statistic missed it.** Least squares with an intercept
forces residuals to sum to zero *overall*, so the model balances across pooled
seasons while being four wins per team low in one and six high in another. Only
a per-season check exposes it. There is a test asserting exactly that: pooled
totals look fine while per-season totals do not.

**What changed.** Centring stayed, with the evidence that it was a correction
rather than a change selected against the test set. Both halves are asserted —
including that the invariant *fails* before the fix — so the evidence cannot rot.

---

## 8. The delta error: two theories, one measurement

**The disagreement.** Two figures shipped for the same quantity, both
theoretical:

- **12.05 wins** — differencing whole-team projections. Assumes the two
  branches' errors are independent. They are not.
- **2.00 wins** — propagating only the changed players' quality. Assumes
  everything team-level cancels exactly. It does not.

At 12, nothing realistic is rankable. At 2, a three-win gap is. The two answers
disagree about the only question anyone asks.

**Measured** against 180 real team-season transitions, each fitted only on prior
seasons:

| sample | n | bias | **sd** | MAE | r |
| --- | --- | --- | --- | --- | --- |
| all transitions | 180 | −1.48 | **10.48** | 8.20 | +0.49 |
| low disruption | 30 | −3.98 | **7.40** | 7.03 | +0.24 |
| *null: always predict 0* | 180 | — | 11.99 | 9.17 | — |

**The pessimistic theory was nearly right.** The earlier claim that realistic
options had become rankable was withdrawn.

The model does carry signal — MAE 8.20 against 9.17 for predicting no change,
and r = +0.49. Directionally useful, quantitatively loose.

**What changed.** The separation threshold, now **10.5 wins**, set from the
measurement rather than from an assumption. Three options projected at 44.2,
42.9 and 41.4 come back as one tier: *the projection cannot rank these, choose
on basketball grounds.*

---

## 9. The leak in our own ingest

**Found by the audit, not by review.** The freeze partition guards the evidence
file. It does not guard the snapshots. Our 2025-26 transaction log runs to
**2026-07-09** — three days past a July 6 freeze — and the offending row is:

```
2026-07-09   The Golden State Warriors signed Charles Bassey.
```

A Golden State signing, in a backtest whose subject is Golden State's behaviour,
arriving in world state looking like ordinary roster data.

**Two more found the same way.** The scorer was written inside `sim/branch.py`
with the real post-freeze salaries as a module-level dict, one import away from
the planner that must never see them — caught by a test written a milestone
earlier. And the contracts snapshot has no date column at all, so post-freeze
signings can only be excluded by name, which the audit output states as its own
weakest link.

**What changed.** `redact_after` for dated tables, the scorer moved to `eval/`
where reading the answer requires an explicit token, and a test that greps
`sim/` and `agents/` for the literal post-freeze figures.

---

## 10. gpu_fraction 1.0 through a 3x slowdown

**The claim that was true and useless.** Every manifest recorded
`gpu_fraction: 1.0`. The weights really were entirely in VRAM. Throughput was
12 tok/s instead of 36, because background processes had taken the card to 23.5
of 24.6 GiB and left no headroom for compute buffers.

The manifest looked correct, the run completed, and only the latency column was
wrong.

**Third instance of the same mistake.**

| milestone | the claim | what it actually meant |
| --- | --- | --- |
| M1 | `schema_enforced_by_server: true` | we *sent* a schema |
| M1.5 | `enforces_schema()` returns a constant | a claim about all versions |
| M2 | `gpu_fraction: 1.0` | where the weights are, not whether they can run |

**What changed.** A fixed-prompt throughput canary in every manifest, and a
bench that aborts on >15% drift from a stored baseline in either direction. It
keys on generation rate rather than wall clock, because a cold model load is
legitimately slow and would fire the alarm.

---

## 11. The freeze state that manufactured a market

**The symptom.** The first multi-team run gave Miami **all eight** contested
players. It read as a defect in the contention rule — offer-maximisation letting
the richest team sweep.

**The cause was upstream.** Ground truth was "players on this team in 2026-27
who were not on it in 2025-26", which cannot distinguish a signing from a trade.
Giannis Antetokounmpo was in that set, so his $58,456,566 came *off* Miami's
freeze state — handing them roughly $100M of cap space that never existed.

He was traded to Miami on **2026-06-22**, two weeks before the freeze. So were
LaMelo Ball and Josh Green to Minnesota (June 25) and Jaylen Brown to
Philadelphia (July 6, the freeze date itself). All three were inputs.

**What changed.** Arrivals are labelled by mechanism, sourced one at a time with
a URL and a retrieval date, and anything unsourced stays `unknown` rather than
being guessed into whichever bucket flatters the number. Pre-freeze arrivals
stay in the freeze state. Recall is reported signings-only as the headline, with
the all-arrivals figure alongside so the bound is visible.

**And the contention rule changed too**, because offer-maximisation was
independently falsified: it cannot produce LeBron James choosing Philadelphia at
$3,876,529 over a team with cap space. Resolution now runs commitment → roster
tier → offer → arbitrary, with tiers 12 wins wide so every comparison it makes
is one the measured 10.5-win error supports.

---

## What is still unmeasured

- **Whether the LLM plans a better offseason than the deterministic planner.**
  The backtest uses the deterministic one, on purpose, so a failure is
  attributable. The comparison has not been run.
- **Whether thinking mode changes decision quality.** It costs 16x in latency
  (7.4s against 122s on an identical prompt). It was held constant, not varied.
- **The counterfactual branch.** Unfalsifiable by construction. It is run and
  reported, never scored.
