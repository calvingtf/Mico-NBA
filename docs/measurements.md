# The measurement history

Every number that changed the design, what it was, and what changed because of
it. Ordered as they happened, because several of them only make sense as
corrections to the one before.

The through-line: **each measurement was set up so it could come back negative,
and several did.** The ones that did are the reason the architecture looks the
way it does.

---

## 0. The cash limit that was wrong in the brief

Chronologically first, and listed first because it set the rule everything
after it follows.

`cash_limit` — the cap on cash a team may send in a trade — was **assumed to
track the expanded trade-player exception**. It does not. Sourcing it season by
season gave $7,005,000 / $7,240,000 / $7,964,000 / $8,497,000 for 2023-24
through 2026-27, and the values in use were **wrong by $250K–$600K each**.

**The cross-check is what made it safe to overrule the assumption**: every one
of those four figures lands on exactly 5.15% of that season's cap. Four
independent numbers agreeing on one ratio is not a coincidence, and it is a
much stronger warrant than any single citation.

A second, smaller one in the same pass: `non_taxpayer_mle` for 2026-27 was
$15,045,000 against a published $15,044,000.

**What changed.** Every league constant now carries a source and a confidence
rating — `verified`, `derived`, or `unverified` — and
`test_unverified_constants_are_declared` asserts the unverified set is **empty**,
so adding an unsourced number fails the suite. The standing rule dates from
here: *sourced or absent, never recalled.* Contract structure for historical
seasons could not be sourced, so it is absent for them rather than filled in.

A figure being handed to me is not provenance. It is a claim to check.

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

**Third arm, complete.** Naming *which* of the team's own contracts unlock each
target closed the gap outright:

| across all three scenarios | blind | feasible | unlock |
| --- | --- | --- | --- |
| Named an unreachable target | 65.5% | 0.0% | **0.0%** |
| Satisfiable, first attempt | 31.0% | 58.6% | **100%** (23/23) |
| Satisfiable, final | 58.6% | 75.9% | **100%** |
| LLM calls spent | 102 | 99 | **75** |
| Step 3: selections / declines | 13 / 4 | 13 / 9 | **7 / 16** |

Fewer calls, because no revision round was ever needed. Stand-pat rates are
19.4%, 19.4% and 20.7% across the arms, so this is not the model attempting
less and being scored on an easier subset.

**And it did not produce trades.** The model declined 16 of the 23 legal
package sets it was handed. Every intent became satisfiable and step 3 ran
every time; the answer was mostly no. For an apron team whose legal moves are
all cheap-for-cheap swaps that is a defensible read of a thin market — but the
honest summary is that solving feasibility bought informed refusals rather than
activity.

The unlock arm ran 29 trials to the others' 36, because the throughput canary
aborted the last cell. See below.

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

## 12. The canary fires

**Measured, mid-benchmark.** The throughput canary refused to continue:

```
server error: throughput canary is 67% slower than baseline:
              11.72 tok/s against 36.02 tok/s recorded 2026-07-31T17:04:56Z

ABORTING: the machine is not in a state worth measuring on.
```

Seven trials of the third unlock cell were never run.

**Why that is the right outcome.** Those seven trials would have completed.
They would have produced satisfiability figures indistinguishable from the
others and a latency column three times too slow, and nothing in the manifest
would have said so — `gpu_fraction` would have read 1.0 throughout, because the
weights really were in VRAM. That is precisely the failure that corrupted an
earlier milestone's latency numbers and went unnoticed until a scenario ran six
times slower than the one before it.

The cost is a smaller sample in one cell, stated wherever that cell is quoted.
The alternative was a full sample that was quietly wrong.

---

## 13. Deadline disposition, measured instead of borrowed (M8)

**The defect.** `models/disposition.py` set its buyer/seller bands from
`MEASURED_DELTA_SD` — the value model's win-delta error. Two tests asserted
this was correct, one of them demanding that a *majority* of teams come back
`AMBIGUOUS`, and both ran green for a whole milestone.

It is the wrong error bar. 10.5 wins is uncertainty on a **counterfactual
roster delta**. Disposition depends on record and games back **on the freeze
date** — completed facts with no projection in them. Borrowing a projection's
spread to threshold an observed quantity is a category error, and it cost:
23 of 30 teams landed `AMBIGUOUS`, ambiguous teams acted on neither side, so
the simulation proposed nothing between any two middling teams.

**Measured.** 90 team-seasons across the 2023, 2024 and 2025 deadlines —
games back at the deadline against finishing top ten in conference:

| games back at deadline | n | made top 10 |
|---|---|---|
| 8+ clear | 20 | 100% |
| 4–8 clear | 17 | 100% |
| 0–4 clear | 20 | 85% |
| 0–3 back | 15 | 40% |
| 3–6 back | 4 | 0% |
| 6+ back | 14 | 0% |

The edges are clean: **37/37** of teams 4+ games clear made it, **18/18** of
teams 3+ back did not. `SELLER_GAMES_BACK = 3.0`, `BUYER_GAMES_AHEAD = 4.0` —
far tighter than 10.5, because an observed standing is far better determined
than a projected delta. At the 2025 deadline: 12 buyers, 6 sellers, 12
ambiguous, against 4/3/23 before.

`test_disposition_never_consults_the_value_model` parses the module's imports
and fails on any path to `value`, `win_delta`, `compare` or `delta_error`.
Asserted on the source, not by monkeypatching: a spy passes for a module that
imports a projection and merely happens not to call it on the tested input.

## 14. The trade denominator cannot be raised without multi-team support

A coverage pass on why the deadline backtest scores against so few real trades.
Every trade row in a 14-day window before each deadline:

| season | rows | 1 team | 2 teams | 3+ teams | parsed | priceable |
|---|---|---|---|---|---|---|
| 2023-24 | 20 | 16 | 2 | 2 | 1 | 0 |
| 2024-25 | 19 | 13 | 1 | 4 | 1 | 1 |
| 2025-26 | 28 | 19 | 4 | 5 | 4 | 3 |

**Pooled denominator: 4.** Single digits, as feared, and the diagnosis is not
the one expected. The loss is *not* at the pricing stage — nothing in-window
was dropped for a missing salary. Every single-team row is a picks/cash/
trade-exception addendum carrying no players at all:

```
teams='BOS' players=''
   The traded FROM_TRADE to the Boston Celtics . 2027 2nd-rd pick is least
   favorable 2030 2nd-rd pick is BOS own ... Boston received a trade exception
```

The binding constraint is the **two-team restriction**. Real deadline business
is multi-team: on 2025-02-06 there were 13 trades and not one was a two-team
deal with players moving both ways. Extending the contract ingest — the fix
this pass was expected to produce — would have recovered nothing.

**So no precision claim is made from this denominator.** Four is not a sample.
Recall of 1/4 is reported as a count, not a rate.

## 15. What the deadline planner actually gets wrong: precision

The number that does survive, because its denominator is *proposals*:

| | 2023-24 | 2024-25 | 2025-26 | pooled |
|---|---|---|---|---|
| proposed | 213 | 208 | 0 | **421** |
| real two-team trades | 0 | 1 | 3 | 4 |
| counterparty pairs matched | 0 | 1 | 0 | **1** |
| solver legality on real trades | — | 1/1 | 3/3 | **4/4** |

**421 proposals against roughly fifteen real deadline trades.** The planner
enumerates legal permutations; it does not model a market. Adding prior-season
value fixed the worst of it — ordering targets by cost had produced Jayson
Tatum and Payton Pritchard for Zion Williamson — but the first run *with*
values still sent Stephen Curry and Joel Embiid to Atlanta on the same tick,
because the value gate constrains only the acquiring side and value is close to
zero-sum. `_will_part_with` closes it: a seller parts with anyone, a team still
in the race parts only with a below-median player. Proposals fell from 380 to
208 and the absurdities went with them. It is still two orders of magnitude too
many, and that is the honest headline.

**Two defects found while running this.** `run(freeze=FREEZE, season=SEASON)`
defaulted the freeze to a fixed date regardless of the season asked for, so
`run(season="2025-26")` planned an offseason 365 days from its own deadline and
returned zero proposals — invalidating two rows of the first pooled table
computed here. And the game-log ingest does not cover 2025-26, so that season
has no standings, no dispositions, and no proposals at all. The first is fixed;
the second is a data limit, stated rather than filled in.

---

## 16. The symmetric gate: an experiment that changed nothing

The last build experiment, run to settle whether the deadline planner's
precision problem was still a modelling gap or had become a data-resolution
limit.

**The change.** The supplier-side gate asks whether a team would really part
with a player: a seller parts with anyone, a team still in the race parts only
with a below-median one. That test was applied to the counterparty only. The
experiment applies it **symmetrically** — the proposing team's outgoing package
must also survive its own disposition. Deterministic, no extra LLM calls.

| | proposals | matched | precision |
| --- | --- | --- | --- |
| asymmetric (default) | 421 | 1 | 0.24% |
| **symmetric** | **415** | 1 | **0.24%** |

**Six proposals removed. Precision identical to two decimal places.**

**What it settles.** The remaining false positives are not trades a disposition
test can reject. They are legal, plausible-looking swaps between teams that
would genuinely consider them, and separating them needs value resolution finer
than this project has: the measured delta error is **10.48 wins** (entry 8), and
deadline trades turn on differences far smaller. The evaluation layer cannot
score the distinction either, with **4** scoreable trades pooled (entry 14).

**What changed.** `SYMMETRIC_GATE = False`, left in place and off, with this
measurement named in the comment. The README's first limitation — *there is no
market model* — is stated as measured rather than assumed on the strength of
this: two named causes were fixed, a third refinement was tried, and precision
did not move.

The useful negative result is not that the idea failed. It is that the failure
localises the limit to value resolution, which is a different project.

---

## What is still unmeasured

- **Whether the LLM plans a better offseason than the deterministic planner.**
  The backtest uses the deterministic one, on purpose, so a failure is
  attributable. The comparison has not been run.
- **Whether thinking mode changes decision quality.** It costs 16x in latency
  (7.4s against 122s on an identical prompt). It was held constant, not varied.
- **The counterfactual branch.** Unfalsifiable by construction. It is run and
  reported, never scored.

---

---

## 17. The pair metric was measuring proposal volume, not skill

Counterparty matching read **5 of 5** at the deadline, which looks like the one
thing the planner does well. It is not a result. It had never been tested
against a null, and the null destroys it.

**Coverage first.** There are C(30,2) = 435 team pairs. The planner's proposals
cover:

| season | proposals | distinct pairs | % of pair space |
|---|---|---|---|
| 2023-24 | 213 | 212 | **48.7%** |
| 2024-25 | 208 | 206 | **47.4%** |
| 2025-26 | 0 | 0 | 0% |
| pooled | 421 | 302 | 69.4% |

Essentially one proposal per distinct pair, covering half the league. And a
three-team trade is matched by any of its C(3,2)=3 constituent pairs, so each
real trade has three chances rather than one.

**Exact null.** Drawing the same number of distinct pairs uniformly at random:

| trade | qualifying pairs | P(hit by chance) | observed |
|---|---|---|---|
| IND/PHI/SAS | 3 | 0.866 | HIT |
| BKN/PHX/MEM | 3 | 0.866 | HIT |
| LAC/UTA | 1 | 0.474 | HIT |
| CHI/SAC/SAS | 3 | 0.855 | HIT |
| MEM/SAC/WAS | 3 | 0.855 | HIT |

**P(all five matched by chance) = 0.260.**

**Monte Carlo, 20,000 trials**, same proposal counts, pairs drawn at random:

```
matches   0      1      2       3       4       5
share   0.0%   0.5%   4.7%   22.5%   46.1%   26.1%
```

Null mean **3.92 of 5**; observed 5. **P(null >= observed) = 0.261.**

**Precision is worse than that.** Only 13 of 435 pairs would count as a hit at
all, so a random proposer's expected precision is **2.99%**. The planner's
observed precision is **2.14%** — *below* chance.

**What changed.** The counterparty-match figure is relabelled everywhere it
appears. It measures how much of the pair space 421 proposals cover, which is
half of it, and not the planner's ability to identify who trades with whom. A
metric that a random proposer beats is not evidence about a planner.

This is the third time a number in this project looked like a result and turned
out to be an artifact of how it was computed — after the 200% recall and the
5-of-5 legality rate that counted UNDETERMINED as legal. The pattern is
specific: **a numerator and a denominator that were never asked what they would
do if the system did nothing.**

## 18. Recall, split by whether the planner could run at all

Pooled recall of 5 of 13 (38%) averages two incomparable things.

| seasons | real trades | matched | recall | why |
|---|---|---|---|---|
| 2023-24, 2024-25 | 5 | 5 | **100%** | planner ran |
| 2025-26 | 8 | 0 | **0%** | planner proposed nothing |
| pooled | 13 | 5 | 38% | — |

2025-26 has no game-log ingest, so `standings_on()` returns empty, every team
comes back with no disposition, and no team acts. Zero proposals is not a
failure to identify trades — it is the planner never running.

So neither figure is quotable alone. 100% is a null-level result on five
trades (entry 17). 38% is that same result diluted by a season where the input
was missing. **The pooled number is reported with the split beside it, always.**

Fixing 2025-26 needs one thing: the `nba_stats` game-log ingest extended to
that season. Every other input for it is already present — contracts,
transactions and 8 scoreable real trades are sitting there unused.

---

## 19. The legality rate had no null either, and it is worse than one

The README carried "validator legality on real trades: 5 of 5". Asked the
standing question — *what would a system that does nothing score?* — it falls
apart in two separate ways.

**Every real trade in the test set is legal.** The league approved them. So a
validator that approves everything unconditionally scores **100%**. That is the
null, and it is the ceiling: this test set cannot reward catching an illegal
trade, because it contains none.

**Against that null, ours scores 70%** (7 of 10 not rejected). It is *below* the
do-nothing baseline, which is the only direction possible on a set of known-legal
trades. What the figure actually measures is a **false-rejection rate of 30%**.

**And the 70% is itself inflated**, because it counts `UNDETERMINED` as legal:

| verdict | n |
|---|---|
| APPROVED | **0** |
| UNDETERMINED | 7 |
| REJECTED | 3 |

**Zero real trades are approved outright.** Every non-rejection is an
undetermined — base-year compensation that needs a re-sign status the ingest
does not carry, or a pre-2023 figure that was never sourced. With
`UNDETERMINED` excluded the rate is **0 of 3**.

**What changed.** The metric is relabelled as a false-rejection rate, which is
what it measures, and the 5-of-5 phrasing is gone. Demonstrating that the
validator *catches* illegal trades needs a test set containing illegal trades —
the synthetic coverage matrix from M0 does that job, and the two are no longer
conflated. Note also that the rate was 5 of 5 on two-team trades and 7 of 10 once
three-team trades were parsed: the new denominator brought in the first cases that
exercise second-apron aggregation, and the validator got some of them wrong.

## 20. The null audit

Every number in the README, against the standing rule.

| metric | value | null | verdict |
|---|---|---|---|
| LLM legal trade proposals | 0 of 12 | ~0 by chance | **holds** — the claim is negative and the null agrees |
| Three-arm A/B, unreachable targets | 65.5% → 0% | each arm is the others' control; stand-pat 19.4/19.4/20.7% | **holds** — controlled comparison |
| Value model MAE | 7.49 | 7.99 (regress to .500) | **holds**, and reported as not beating it (p=0.159) |
| Win-delta error sd | 10.48 | 11.99 (always predict 0) | **holds** — carries signal, r=+0.49 |
| Scheduler wake savings | 74% | polling every agent, the thing measured against | **holds** |
| Deadline precision | 2.97% | 6.67% random proposer | **fails** — below chance |
| Deadline counterparty match | 11 of 13 | 10.18 expected, P(null≥obs)=0.426 | **fails** — indistinguishable |
| Validator legality | 70% | 100% approve-everything | **fails** — below chance; see entry 19 |
| Signing backtest precision | 14.3% | ~2.1% drawing from the 522-player pool | **holds, weakly** — beats a naive null ~7x, but the pool is generous to us; a tighter null over only the real free-agent pool would be higher and has not been computed |

Three of nine fail. All three were headline numbers.

---

## 21. The sixth check that certified a different surface than its claim

The README said, in two places and in `agents/gm.py` in a third:

> The model never sees a salary, never emits a package, never states terms.

Two thirds of that is true. The first third was false for four milestones, and
`tests/test_boundary.py` passed the whole time.

**Found by accident.** Claude Haiku's stand-pat reasons in the M10 calibration
run cited a figure: *"acquiring him would require trading away multiple core
rotation pieces to match his $55.7M salary."* Checking whether the model had
recalled that from training or been handed it, the answer was handed it —
**29 money strings in the `action_choice` prompt**:

```
Your team: LAL   payroll $187,502,042   apron status: first apron
```

`RosterEntry.render()` emits `${salary:,}` for **every player on both rosters**,
and `CONTEXT_TEMPLATE` adds payroll and apron tier. Not one target salary — the
complete salary book for both teams, on the first call of every arm including
blind.

**Why the test missed it.** `test_boundary.py` asserted on agent-facing
*schemas* (no field can hold a salary) and on model *outputs* (the model never
supplies a figure). Both still pass, and both are about what the model can
**say**. The claim was about what the model can **see**. Nothing rendered a
prompt and looked at it.

**What changed.** `TestWhatTheModelSees` renders `CONTEXT_TEMPLATE` and asserts
money *is* present — recording the real behaviour, because a claim of absence
with no test either way is how this survived. A second test greps the README and
fails if the salary-blindness claim reappears, allowing a quoted retraction and
nothing else. The README now says what is true: the model sees payroll, apron
status and both rosters' contracts, and cannot emit a package or state terms.

**The pattern, sixth instance.** After a 200% recall, a legality rate counting
UNDETERMINED as legal, a counterparty match a random proposer beats, a canary
recorded as passing on a hosted model, and sampling params recorded as values
never sent — this is the same failure again: **a check that certifies a
different surface than the claim it is trusted to support.** The charter rule
gains a companion: no claim in the README without a test asserting the surface
the claim is actually about.

**What it does not invalidate.** The blind/feasible/unlock A/B remains internally
valid — all three arms shared this context, so the contrast between them is
unaffected. What is wrong is the word "blind", which never meant salary-blind,
and the M10 question as posed. A genuinely salary-free fourth arm is the way to
ask it properly.

---

## 22. M10: on the constrained scenario, no model succeeds unaided

**First reported wrong.** The M10 table put Qwen's *pooled three-arm* figures
(31% satisfiable, 65.5% unreachable) in a column against *per-scenario* hosted
numbers, and concluded that capability substitutes for scaffolding. Rebuilt per
scenario from the recorded manifests:

### curry-to-lakers — Los Angeles, above the first apron

| model | arm | acted | 1st-attempt satisfiable | final |
|---|---|---|---|---|
| Qwen 27B | unaided | 12/12 | **0/12** | **0/12** |
| Sonnet 5 | unaided | 12/12 | **0/12** | **0/12** |
| Opus 5 | unaided | 12/12 | **0/12** | **0/12** |
| **Qwen 27B** | **unlock** | **21/21** | **21/21** | **21/21** |

**No model succeeds unaided. Not one, at any capability.** The frontier models
are not better than the 27B here — all three are at zero. The scaffolding takes
the *same weak model* to 100%.

### mid-flexibility-bulls — Chicago, over the cap, clear of the aprons

| model | arm | 1st-attempt | final |
|---|---|---|---|
| Qwen 27B | unaided | 5/12 | 12/12 |
| Sonnet 5 | unaided | 11/13 | 12/13 |
| Opus 5 | unaided | 9/24 | 23/24 |

### positive-control-pistons — under the cap, ten legal packages verified

| model | acted | 1st-attempt | final |
|---|---|---|---|
| Haiku 4.5 | 11/12 | 2/12 | 8/12 |
| Qwen 27B | 14/14 | 5/14 | 8/14 |
| Sonnet 5 | 12/12 | 0/12 | 12/12 |
| Opus 5 | 12/12 | **12/12** | **12/12** |

**The conclusion, corrected.** Capability substitutes for scaffolding where
constraints do not bind — Opus goes 12/12 first-attempt on the control where
Qwen manages 5/14. **Where constraints bind, only the scaffolding works.** Every
model is at 0/12 unaided on the apron scenario and Qwen+unlock is at 21/21.

That is the stronger claim for the architecture, and it is the opposite of what
was first reported. The error made the flattering-to-capability reading look
true; the correction makes the flattering-to-the-design reading true. Both were
found by rebuilding from manifests rather than by re-reading the summary.

**Seventh instance of the pattern**, and a new species of it: not a check
certifying the wrong surface, but **a number valid at one scope quoted at
another**. Pooled across three arms, 31% is correct. Placed in a per-scenario
column it asserts something the data never said. After the 200% recall, the
UNDETERMINED-as-legal legality rate, the counterparty match, the canary on a
hosted model, sampling params never sent, and the salary-blindness claim.

## 23. Personas may be text the model recites, not a disposition it acts from

Rate at which stated reasons cite a persona parameter by name
(`asset_hoarding`, `risk_tolerance`, `win_now_horizon`):

| model | rate |
|---|---|
| Qwen 27B | **78.3%** (317/405) |
| Sonnet 5 | 67.7% (84/124) |
| Haiku 4.5 | 61.2% (41/67) |
| Opus 5 | **50.6%** (80/158) |

Every model cites them, so this is not a Qwen artifact. But the rate falls
monotonically with capability, and the *content* differs: Qwen restates the
numbers as the justification — *"With an asset_hoarding of 0.8 and
risk_tolerance of 0.3, preserving our young core is paramount"* — while Haiku
and Opus reason about cap state and roster fit and mention the parameters less.

Those are different objects, not different thresholds on one axis.

**What it implies.** Every persona-driven result in this project assumes the
persona is a disposition the model acts *from*. On the evidence it may be, for
the weaker model, text it recites *at* the decision it was going to make
anyway. Nothing here separates the two, and the charter's anti-goal on prose
personas was aimed at a different failure than this one. Any future claim that
a persona changed behaviour needs a control where the parameters are permuted
and the outcome is measured — which has not been run.

---

## 24. Re-scoring at scale: 98 legality-scoreable, but still 13 deadline positives

Ten seasons ingested, CBA-era machinery in place. The gate for everything else.

| season | era | parsed | representable | scoreable | APPR | UND | REJ |
|---|---|---|---|---|---|---|---|
| 2016-17 | 2017 | 59 | 34 | **0** | 0 | 0 | 0 |
| 2017-18 | 2017 | 44 | 31 | 20 | 0 | 15 | 5 |
| 2018-19 | 2017 | 63 | 44 | 26 | 0 | 22 | 4 |
| 2019-20 | 2017 | 43 | 30 | 17 | 0 | 12 | 5 |
| 2020-21 | 2017 | 24 | 14 | 7 | 0 | 5 | 2 |
| 2021-22 | 2017 | 36 | 22 | 10 | 0 | 10 | 0 |
| 2022-23 | 2017 | 16 | 10 | 4 | 0 | 3 | 1 |
| 2023-24 | 2023 | 23 | 8 | 4 | 0 | 1 | 3 |
| 2024-25 | 2023 | 31 | 21 | 4 | 0 | 3 | 1 |
| 2025-26 | 2023 | 26 | 18 | 6 | 0 | 4 | 2 |
| **pooled** | | **365** | **232** | **98** | **0** | **75** | **23** |

**Scoreable trades 13 -> 98.** `APPROVED` remains **0** at every scale: no real
trade is ever approved outright, because base-year compensation needs a re-sign
status the ingest does not carry. The legality figure is a false-rejection rate
against a 100% approve-everything null, unchanged in kind by the larger n.

**2016-17 scores zero** of 34 representable trades: 18 are draft-rights deals
and 16 hit players with no salary row. Reported, not worked around.

### The era split does not diverge significantly

| era | n | legal | rate |
|---|---|---|---|
| 2017 CBA | 84 | 67 | 79.8% |
| 2023 CBA | 14 | 8 | 57.1% |

A 22.7-point gap, and the instinct is to hunt for a wrong era rule. Two-proportion
test: **z = 1.85, p = 0.064.** Not significant at n=14. **No era rule can be
called wrong on this evidence**, and looking for one would be fitting noise.
The 2023-era rejections are two `SALARY_MATCH`, two `ROSTER_LIMIT` (an input
gap - roster size on the date is not in the ingest) and one
`AGGREGATION_SECOND_APRON`.

### The deadline denominator did NOT move

| | proposed | actual | matched | precision | null | delta |
|---|---|---|---|---|---|---|
| pooled, 3 seasons | 673 | **13** | 11 | 2.97% | 6.67% | **-3.69 pts** |

Only 2023-24, 2024-25 and 2025-26 have standings coverage; `CALENDARS` carries
no earlier season and the game-log ingest starts at 2022-23. The backfill added
contracts and transactions, not calendars or game logs.

**So the two denominators are different quantities and must not be conflated.**
98 is legality-scoreable trades across ten seasons. **13 is deadline positives**,
and it is unchanged. Precision remains 3.7 points *below* its null.

**Consequence: the ranker is not unlocked.** Its positives are real deadline
trades and its negatives are generated proposals, so it is bounded by the three
seasons where the planner runs - 13 positives against 653 negatives. At 13
positives a classifier fits noise, which is the stated gate. Raising it needs
calendars and game logs for the backfilled seasons, not more model capacity.

---

## 25. Deriving re-sign status: APPROVED 0 -> 33

`APPROVED` was **0 at every scale** — 12 trades, 98 trades, it never moved.
Not because the validator disliked real trades, but because base-year
compensation could never be *ruled out*: the ingest carried no re-sign status,
so every trade returned UNDETERMINED on a question nothing could answer.

Ten seasons make it answerable. Three cases, and only the third stays open:

| evidence | conclusion |
|---|---|
| changed team between seasons | signed as an outside free agent, not re-signed — BYC cannot apply |
| same team, raise ≤ 20% | the BYC raise threshold is not met |
| same team, raise > 20% | genuine BYC candidate — `RE_SIGNED_BIRD` |
| no prior season on file | **`UNKNOWN`**, left open |

Over 4,068 consecutive-season player rows: 1,578 changed team, 1,807 stayed
with a raise of 20% or less, 683 are candidates. **83.2% resolvable by
evidence.**

| | before | after |
|---|---|---|
| APPROVED | **0** | **33** |
| UNDETERMINED | 75 | 42 |
| REJECTED | 23 | **23** |

**REJECTED does not move.** That is the check that this resolved open questions
rather than manufacturing permissions — a derivation that turned rejections into
approvals would be laundering, and this one cannot: the candidate case returns
`RE_SIGNED_BIRD`, which keeps the restriction *on*.

Per era: 2017 CBA n=84 → 26 / 41 / 17. 2023 CBA n=14 → 7 / 1 / 6.

**The null is still 100%.** Every trade in this set was made and approved by the
league, so a validator that approves everything unconditionally still scores
perfectly, and this remains a false-rejection rate — now **23/98 = 23.5%**.
What changed is the ceiling on what the metric can *say*: it can now
distinguish "approved" from "could not tell", which it could not before. It did
not become a skill measure and no amount of derivation would make it one on a
test set containing no illegal trades.

---

## 26. The pooled null was inflated, and it ran against us

**Instance #11, and the first that made a result look worse than it was.**

The pooled precision null was computed as the union of qualifying team pairs
across seasons, divided by the 435-pair space. But **a proposal made in season S
can only hit a trade in season S.** A 2016-17 pair is not available to a 2024-25
proposal, so unioning credits the null with pairs no proposal could ever have
hit. The null is a per-season quantity; pooling it means weighting each season's
null by how many proposals that season contributed.

| three-season pooled | value |
|---|---|
| null as reported (union ÷ 435) | 6.67% |
| **null, corrected** (proposal-weighted) | **2.40%** |
| observed precision | 2.97% |
| reported delta | **−3.70 pts, "below chance"** |
| **corrected delta** | **+0.57 pts, 1.24x chance** |

Per-season nulls were 1.38%, 1.61% and 3.91% — every one of them below the
pooled figure, which is the tell. A weighted mean cannot exceed its largest
input, and a test now pins that.

**1.24x on 13 positives is not a result either.** The correction does not turn a
failure into a success; it turns a wrong negative into an untested marginal.
The claim "the planner does not beat a random proposer" was wrong as stated and
is retracted, and no replacement claim is made until the pooled permutation test
runs at n=71.

### Why this one survived longest

Every prior entry flattered a result, and flattering numbers get audited: they
invite disbelief. This one said we had failed. Several rounds of work went into
*raising* precision — a disposition fix, a value model, a supplier-side gate, a
symmetric gate — and not one of them questioned the denominator that work was
being measured against. A metric reporting failure is taken as a problem with
the system rather than a problem with the metric.

**Standing rule, now in the README:** a metric at or below its null gets the
same audit as one that beats it. Failure is not self-certifying.

---

## 27. Join hit-rate audit: no silent 100% miss, but the joins are lossy

Instance #13 was a lookup whose default fired on **100%** of keys and produced a
plausible number. `data/joins.py` now records matched/total for any lookup with
a fallback and raises past a *declared* tolerance — a declaration, not a tuning
knob, so a join that legitimately misses 40% declares 45% and a regression to
90% still raises.

Audited on the keys that matter: **players actually in trades**, since those are
the rows whose verdicts get published.

| join | 2018-19 | 2021-22 | 2024-25 |
|---|---|---|---|
| service years (nba_api → bbref, by normalised name) | 72.5% | 67.9% | **62.3%** |
| re-sign status (year-over-year contracts) | 76.3% | 51.3% | **50.6%** |
| player values (stats → bbref) | 67.2% | 48.7% | **46.8%** |

**No join is silently missing everything**, which was the specific failure mode
#13 represented. League-wide the service-years join runs 92.6–93.4%.

**But on traded players it is far worse**, and that is the population that
matters. Roughly a third to a half of traded players carry no service years, no
derived re-sign status, or no value. Traded players skew toward the recently
signed, the two-way, and the just-waived — exactly the players a
year-over-year or name-normalised join loses.

### Does anything need restating?

**Not the legality figures, and the reason is structural rather than lucky.**
Every one of these joins fails *toward* `UNKNOWN`, not toward a permission:

* Missing service years → the minimum-salary exception cannot be evaluated →
  the player is matched normally, which is the conservative direction, and the
  trade returns `UNDETERMINED` rather than approved.
* Missing re-sign status → `ReSignStatus.UNKNOWN` → BYC stays open →
  `UNDETERMINED`.
* Missing value → the player is not preferred by the planner, so a proposal is
  not made rather than made wrongly.

So the 23.5% false-rejection rate and the APPROVED 0 → 33 movement stand. What
the audit *does* explain is why `UNDETERMINED` remains large at 42 of 98: it is
not only base-year compensation, it is a join that loses a third of its keys.

**What would need restating** is any future claim that rests on a join failing
toward a *decision*. None currently does, and the helper exists so that the next
one raises instead of producing a plausible number.
