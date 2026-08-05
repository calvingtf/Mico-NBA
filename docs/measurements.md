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

---

## 28. Missingness differs by class, so the ranker would detect the join

Measured before fitting anything, because a model trained on this would score
well and be measuring the wrong thing.

| season | feature | missing on positives | missing on negatives | gap |
|---|---|---|---|---|
| 2018-19 | value | **17.3%** | **0.0%** | +17.3 |
| 2018-19 | service years | 11.5% | 0.0% | +11.5 |
| 2024-25 | value | **11.8%** | **0.0%** | +11.8 |
| 2024-25 | service years | 11.8% | 0.5% | +11.3 |
| 2021-22 | all | 0.0% | ~0.0% | ~0 |

**Negatives are 0% missing by construction.** The solver only proposes players
it can value — that is what `_will_part_with` does — so every generated negative
carries a complete value feature *necessarily*. Positives are real trades and
include players the join loses.

So `value is missing` is very nearly a label. A ranker given these features
would learn *"no value ⇒ real trade"*, score respectably, and be detecting the
join's coverage rather than anything about trades. The gap here is smaller than
feared — 12–17%, not 30–50% — because deadline-window trades skew toward
established players, but a 12-point one-directional gap on a 1.4x effect is more
than enough to manufacture the whole result.

**Imputation cannot fix this.** The usual remedy assumes both classes have
missing values to impute. Here one class has none by construction, so any
imputation still leaves the *indicator* perfectly informative, and adding a
`was_missing` flag makes it worse rather than better.

**Decision: restrict positives to those with complete features, and report the
cost.** That drops 12–17% of positives and biases the remainder toward
established players — a real limitation, stated rather than hidden. The
alternative, dropping value entirely, removes the only feature with a plausible
causal story about why a trade happens.

**2021-22 shows a zero gap on n=3 and is not evidence of anything.**

Nothing is fitted until this is applied. The ranker harness exists; the fit does
not, and this is why.

---

## 29. The degree-preserving null, and why it is not yet the deciding test

Run with genuine weighting — 50 distinct pair weights, team trade frequency
ranging 1 to 11, verified non-degenerate:

| null | mean | sd | ratio | normalized headroom |
|---|---|---|---|---|
| uniform | 2.58% | 0.247 | 1.41x | +1.09% |
| degree-preserving | 2.59% | 0.243 | 1.41x | +1.08% |

**They agree to within noise, and that is not the reassurance it looks like.**

The question the degree-preserving null exists to answer is: *did the planner
merely learn which teams are active?* Answering it requires giving the null
proposer the planner's own bias toward active teams, then asking whether the
planner still beats it.

**This construction weights the wrong side.** It weights which pairs *qualify*
by team frequency, while the null proposer's coverage is still drawn uniformly.
So the null proposer has no team-activity bias at all, and the test cannot
detect a planner that has one. The two nulls agreeing is explained by that, not
by the planner's advantage being independent of team activity.

**The claim that survives is therefore the weaker one:**

> **Better than proposing pairs at random.** Pooled precision 3.64% against a
> 2.58% uniform null, 1.41x, +1.09% normalized headroom.

**Not established:**

> ~~Identifies who trades with whom.~~

To establish it, the null proposer must draw its *coverage* from the same
team-frequency distribution the planner exhibits — measure the planner's own
per-team proposal counts, sample the null's covered pairs weighted by those, and
re-score. A planner that only learned team activity would then match the null.
That has not been run.

**Fourteenth entry, and the first where a mechanism was built correctly and
still answered a different question than the one asked.** The weighting is real,
the degeneracy guard works, the variance is non-trivial — and it is on the wrong
side of the comparison. Correct machinery, wrong wiring.

---

## 30. The activity explanation is ruled out; the effect is real and small

Closed in one round, per the decision rule: measure the bias, then rewire only
if it is there.

**Step 1 — is the bias there?** Correlate the planner's per-team pair
participation against real trades per team:

| pooled r | per-season range |
|---|---|
| **+0.342** (n=30 teams) | −0.094 to +0.572 |

Moderate, not near zero. So the planner *does* favour teams that really trade,
and the null needed rewiring rather than a shrug.

**Step 2 — rewire and re-score.** The null proposer's *covered* pairs are now
drawn weighted by the planner's own per-team proposal counts, so a planner that
learned only team activity would match it:

| null | mean | sd | ratio | normalized headroom | p |
|---|---|---|---|---|---|
| uniform | 2.57% | 0.252 | 1.41x | +1.09% | <0.0001 |
| **planner-bias-preserving** | **2.57%** | 0.244 | **1.41x** | **+1.09%** | <0.0001 |

**Unchanged.** Handing the null the planner's own team bias does not help it.

**Why**, and this is the part that makes the result interpretable rather than
lucky: the planner covers roughly half the 435-pair space every season. At that
coverage a team-level bias barely changes the overlap with qualifying pairs —
there is not enough selectivity left for the bias to express itself. The bias is
real (r=+0.342) and it is not doing the work.

### The claim that now survives

> **Better than proposing pairs at random, and not because it learned which
> teams are active.** Pooled precision 3.64% against 2.57%, **1.41x**,
> p<0.0001, and the advantage survives a null given the planner's own
> team-activity bias.

> **Normalized headroom is +1.09%.** The planner closes about one percent of the
> distance between chance and perfection. The ratio and the headroom describe
> the same result and must be quoted together: 1.41x sounds like a finding,
> +1.09% says how small a finding it is.

This is the first claim in the project to survive a null it was designed to
fail. It is also, on the headroom, a very small effect — and both halves of that
sentence belong in the README.

---

## 31. Representability, defined blind, disagrees with the scorer

The rule was written from `deadline.py`'s constraints — pairs only, both teams
need a disposition, every moving player needs a value, one side must be a
supplier — **without reference to which trades were hit.** That was the point,
and it immediately paid for itself.

| positives | count |
|---|---|
| all | 71 |
| representable | **34 (47.9%)** |
| excluded | 37 |

| exclusion reason | n |
|---|---|
| 3-team trade; the planner emits pairs only | 20 |
| 1 player with no value | 10 |
| neither side is a supplier | 4 |
| 2 players with no value | 3 |

**And then the arithmetic broke: recall over representable positives came out
56/34 = 165%.**

A ratio above 1 is not a result, it is a contradiction, and it localises
exactly: the scorer counts a two-team proposal as hitting a three-team trade
when both its teams were really in it — a decision recorded when three-team
parsing landed, and one that makes the number *easier*. The representability
rule says a three-team trade is unreachable. Both statements are defensible and
they cannot both govern the same denominator.

**Which is wrong?** The rule, on this specific point. The planner cannot
*construct* a three-team deal, but under the scorer it can *hit* one, and
recall's denominator must contain what the scorer can credit. The rule is
right about value and disposition and wrong about team count.

**Not corrected here.** Adjusting a blind-defined rule immediately after seeing
which trades it excludes is precisely what defining it blind was meant to
prevent. The fix is to align the *scorer* and the rule deliberately — either the
scorer stops crediting three-team hits, or representability admits them — and
that is a decision about what recall means, not a patch.

**So the headline recall stands unchanged at 78.9% over all 71 positives**, and
no representable-only figure is reported. What is reported is that **52% of
positives are unreachable by the enumerator for stated structural reasons**,
of which the largest single cause is that half the real deadline market is
multi-team and the planner is not.

Sixteenth entry. The first found by a rule refusing to agree with a number that
had already been published.

---

## 32. The ranker does not beat a random ranker

Fitted, reported, negative.

**Setup.** 2101 captured proposals across ten seasons, restricted to
complete-feature rows: **1637 rows, 82 positive pairs, 61 distinct trades**.
75% of positives survive the restriction. Leave-one-season-out, never split
within a season. Logistic regression on five standardised features, chosen so
coefficients are readable at this sample size.

**Precision at k, trade level** — a trade counts once however many of its pairs
are surfaced, because a three-team deal contributes three correlated pairs and
ranking all three highly is one insight, not three:

| | p@1 | p@5 | p@10 |
|---|---|---|---|
| **test** (held-out season) | **0.0%** | **6.0%** | **6.0%** |
| train | 0.0% | 2.0% | 3.0% |
| pair level | 0.0% | 6.0% | 6.0% |
| **random ranker** | **5.01%** | **5.01%** | **5.01%** |

**p@1 is 0% in all ten folds.** The top-ranked proposal is never a real trade.

**p@10 of 6.0% against a 5.01% base rate is 1.20x**, on 61 trades across ten
folds. That is inside noise. **The ranker does not beat a random ranker.**

**Train is *worse* than test (2.0% vs 6.0% at p@5).** That is not overfitting —
overfitting looks like the opposite. It is a model that has learned almost
nothing, where the train/test difference is sampling variation on small folds.
Calling it either way would be reading noise.

**Coefficients** (standardised, so magnitudes compare):

| feature | coefficient |
|---|---|
| salary_similarity | **+0.168** |
| value_moving | +0.130 |
| salary_magnitude | −0.119 |
| value_gap | −0.075 |
| roster_slot_distance | −0.064 |

All small. The largest signal is **salary similarity** — teams with comparable
payrolls trade with each other — which is the trivial dominant signal
anticipated, and a finding about what makes trades happen rather than a
disappointment. It is also nearly mechanical: salary matching *requires*
comparable outgoing and incoming money, so the feature partly encodes the
CBA rather than a front office's preferences.

**Per era.** Not reported separately. With 61 trades over ten folds the
per-season cells are 0–20% and the 2023-CBA subset is n=13; splitting further
would report noise with a label on it.

**Stated limitations.** `record_gap` was never populated — standings were not
wired through the capture — and is now declared absent rather than advertised.
The completeness restriction drops 25% of positives and biases survivors toward
established players. Both are named rather than worked around, and neither can
encode the label.

**What this leaves.** The enumerator's own result stands unchanged: **1.41x,
+1.09% normalized headroom, p<0.0001**, surviving a null built from its own
team-activity bias. The retrieve-then-rank idea was sound; the ranking half of
it does not work with these features at this sample size.

---

## 33. The suitor filter excludes the team that signed him

The gate before the thirty-agent run, and it does not pass.

**Reported suitors, from PRE-freeze evidence:** GSW, CLE, LAL, MIA, MIN, PHI —
six teams, drawn from items `LBJ-01`, `LBJ-04` and the `GSW-*` series.

**Hard filter** — does `feasible_signings()` find a legal route to LeBron at the
freeze state? **Admits 6 of 30 teams:** DEN, HOU, LAL, MIN, NYK, ORL.

**It filters hard — 20% of the league — and it filters wrong.**

| | |
|---|---|
| reported suitors admitted | **LAL, MIN** (2 of 6) |
| reported suitors **excluded** | **CLE, GSW, MIA, PHI** (4 of 6) |

**Philadelphia is excluded, and Philadelphia is where he signed.** A filter that
removes the team that actually won the player is not a filter, it is a bug with
good precision. The soft filter was not run: there is nothing to narrow.

### Why, and it is a known limitation rather than a new one

The freeze state is built from the season contracts table, which carries **no
date column**. It therefore includes players signed *after* the freeze, which
inflates every buyer's payroll — the exact exposure the leakage audit already
names as "the weakest link". Philadelphia's own case is documented: correcting
its freeze state moved its offer from $40.9M of cap space to the $15,044,000
mid-level. Here the uncorrected payroll pushes it past the point where any route
survives.

A second signal that the scan is not returning what it appears to: every admitted
team reports `max $0`, so the routes being counted carry no amount. Either the
scan's option objects are being read wrongly at this call site, or the only
surviving routes are minimum-salary ones. Not diagnosed — the filter has already
failed its validation gate and diagnosing it further is next round's work.

### The gate holds

The instruction was to run this check *before* the expensive part, and to treat
"nominates most of the league" as a finding rather than something to tune. The
opposite happened — it nominates a fifth of the league and drops the winner —
and the same rule applies: **this is a finding about the filter, and the
thirty-agent run does not proceed on it.**

Cost estimate, for when it does: the five-team run recorded 17 wakes against 65
polled (74% saved). Under a filter admitting 6 teams the wake count would scale
to roughly 20–25, not 6x — the scheduler wakes on events touching a team's
neighbourhood, and more teams means more events as well as more listeners. The
binding cost is LLM latency, not scheduling: at the five-team run's observed
rate, thirty agents is a multi-hour job and belongs in the background from the
first command.

---

## 34. The $0 was a read bug; the exclusion is real

**Diagnosis: a read bug at my call site, not the solver.**

`FeasibleSigning` carries **no amount, by design** — its fields are
`player_id, name, route_count, routes`, and its docstring says so: *"names and
route labels, never an amount"*, enforced by
`test_feasible_signings_carry_no_money`. That is the same boundary rule that
keeps salaries away from the model. My call site read
`getattr(option, "amount", 0)`, which returns the default for every option
because the field does not exist.

So `max $0` was my read, not the solver's answer. The class docstring stated
the reason and I did not read it before writing the accessor.

**Blast radius: none on the decision.** The admit/exclude test was
`if scan.signings:` — feasibility, not amount — so the 6-of-30 result never
depended on the broken field. Re-run with the correct accessor:

| | |
|---|---|
| admitted | **6 of 30** — DEN, HOU, LAL, MIN, NYK, ORL |
| routes found | 3–5 each: bird, non-taxpayer MLE, taxpayer MLE, bi-annual, minimum |
| reported suitors admitted | LAL, MIN |
| reported suitors excluded | **CLE, GSW, MIA, PHI** |

Identical to before. The routes are named and plural, so the solver is working;
the exclusions are genuine rather than artifacts of a broken read. **The
freeze-state diagnosis stands.**

### The correction cannot be applied league-wide

Entry 33 proposed applying the freeze-state correction and reporting both ways.
The correction that exists is `gsw_freeze_state()` — **hand-worked for one team**
in the LeBron backtest, and defined there *before* any of this filter work, which
is what would have made it admissible evidence. There is no general date filter,
because the contracts snapshot has no date column; that is the limitation, not a
missing feature.

Correcting one team by hand and reporting the filter as fixed would be selecting
the correction to the result. Not done.

### The gate fails again

The run proceeds only if the filter admits the reported suitors and still
excludes a meaningful share of the league. It excludes a meaningful share — 24 of
30 — and it drops four of six reported suitors including the one that signed him.

**The thirty-agent run does not proceed.** What it needs is a dated contracts
ingest, which is a data-collection task and not a modelling one.

---

## 35. Dated contract state: feasible from the transaction log, with a caveat

Scoped before building, and the answer is **positive** — which was not the
expected outcome.

### Route 1: transaction log + season-end contracts, worked backwards

Basketball-Reference's `/contracts/` pages are live views with no archive, so no
fetch produces a dated snapshot. But the transaction log **is** dated, and every
contract row falls into one of two classes:

| class | n (2025-26) | share | dateable? |
|---|---|---|---|
| has a dated transaction | 306 | 58.6% | yes — arrival/departure known |
| has no transaction at all | 216 | 41.4% | yes — present all season by construction |

**Potentially 100%.** A player with no transaction in a season's log was on that
roster for the whole season; a player with one has his movements dated. The
reconstruction is: take the season-end table and, for each player, decide
presence at date *D* from his transaction history.

### The size of what it fixes

**48 players were signed after the 2026-02-05 deadline and still appear in the
season contracts table**, carrying **$126,157,001** — **2.27%** of league
payroll, concentrated on the teams that were active.

That is the inflation the leakage audit calls its weakest link, quantified for
the first time. It is small in aggregate and decisive at the margin: a team
$3M over an apron line is misclassified by it, which is exactly how
Philadelphia lost every signing route in entry 34.

### Route 2: nba_api

Its roster endpoints are season-level (`CommonTeamRoster` takes a season, not a
date) and it exposes no contract or salary data at all — salaries come from
Basketball-Reference precisely because the league API does not publish them. So
nba_api does not solve this, though it is unaffected by the BBRef limitation for
the things it does cover.

### The caveat, which is why this is scoped and not built

The 41.4% "no transaction, therefore present all season" inference is sound
*within* a season, but a player signed in the offseason **before** the season may
have his transaction filed in either league year depending on how the source
dates it. That has not been verified, and it is the class that would silently
misdate a July freeze — which is exactly the LeBron case.

**Method must be defined before it touches the LeBron freeze**, and it has not
been defined yet. Defining it after seeing that Philadelphia is the team that
matters would be the same error the hand-worked correction was kept out for.

**Verdict: feasible, not yet built.** The thirty-agent run is not closed
permanently; it is blocked on a reconstruction whose method must be written and
validated on a season *other* than the one it will be applied to.

---

## 36. The boundary gate passes on aggregate and fails where it matters

Gate 1 for the dated reconstruction, run before any validation and before the
method saw a single team.

**Does the source file transactions by league year, as the rule assumes?**

| | rows | outside their file's league year | rate | rows near July 1 |
|---|---|---|---|---|
| pooled, 10 seasons | 13,760 | **77** | **0.56%** | **1,647** |

**The rule is genuinely tested, not vacuously confirmed.** 1,647 rows fall within
two weeks of a July 1 boundary, so a low disagreement rate is evidence rather
than an artifact of nothing being near the seam. That check was worth insisting
on: a 0% rate with no nearby rows would have looked identical and meant nothing.

Worst seasons are explicable: 2019-20 at 1.8% is the COVID year whose league
year was extended, and 2024-25 at 2.0% files late-June activity under the
upcoming season.

### But the disagreements cluster exactly where the LeBron case sits

| month | disagreements |
|---|---|
| June | 19 |
| **July** | **25** |
| August | 10 |
| all others | 23 |

**June and July hold 57% of all disagreements.** The LeBron freeze is
**2026-07-06** — inside that window.

So the aggregate rate is reassuring and close to irrelevant. A mid-season
validation on a February deadline would exercise the classes that work and
would not touch the class that decides the July case. Passing it would be
evidence about February.

**What this means for the gate.** 0.56% pooled is trivial; 57% concentration in
the two months that matter is not. The honest reading is that the rule is
**sound in-season and unproven at the seam**, and the out-of-sample validation
must therefore include an offseason date — validated against a season whose July
outcomes are already known — rather than a deadline alone.

That is a harder validation than the one specified, and specifying the easier one
was reasonable before this distribution was visible. It is visible now.

**Not stopped, not proceeded.** The disagreement rate does not meet the stated
"non-trivial" threshold for stopping, and the clustering does not permit
proceeding on a February validation. The next step is a July validation on a
prior season, and it is a different test from the one currently queued.

---

## 37. July validation: the method survives, and the suitor gate part-opens

The February test was withdrawn - 57% of boundary disagreements sit in
June-July, so passing it would have licensed nothing about the July case. What
replaced it, in order, all defined before touching the freeze:

### All 77 disagreement rows, inspected

Systematic, not scattered. Four categories: **covid-1920/2021 (26)** - the
source is right and the fixed July-1 rule is wrong for the extended seasons,
harmless because reconstruction reads files, not the rule; **draft-week filed
forward (19)** - almost entirely picks-only rows with no roster effect;
**july-in-closing (10)** - moratorium signings filed in the *closing* season's
file, which is the file a July reconstruction reads, so the class lands in our
favour; **other (22)** - mostly no-roster-effect backfill. Genuine misplacement
risk: ~12 player-placements across ten seasons, 7 of them the 2023-07-06
cluster our use-pattern reads correctly anyway.

### Cross-season consistency, seven seasons

Reconstruction of S at July 10 checked against presence implied by S+1's log -
a table the method never reads. Consistency, not ground truth: it cannot vouch
for players with no S+1 activity, or moves filed in neither log.

| | |
|---|---|
| must-present constraints | 138 |
| **agreement** | **138/138 = 100%** |
| via no-transaction inference | 25 |
| via dated transaction | 113 |
| undateable rows | 0 |

**The seam, quantified.** Departures filed in S+1's log, invisible to
roster_on(S): 58 pooled over July 1-10 windows, **4-9 per league per July 1-6
window** - the freeze sits at the moratorium boundary, where most movement is
dated on or after the freeze itself.

### The suitor check, both ways

| | without dated state | with dated state |
|---|---|---|
| admitted | 6/30 | **8/30** |
| reported suitors admitted | LAL, MIN | **LAL, MIN, PHI** |
| **Philadelphia** | excluded | **admitted - 5 routes** |

**PHI's admission was the roster count, not the payroll.** Its payroll moved
only $727,637; its roster count moved 16 to 14. Season tables count everyone
who appeared; the dated filter removes post-freeze arrivals, and the signing
solver had been reading "roster full" off table bloat.

**CLE, GSW, MIA remain excluded, all by one cause:** season-end rosters "full"
(15-16) of contracts that expired June 30 - an expiry the 2025-26 season table
cannot express. The `bbref-contracts-2026-27` structure snapshot carries end
years and could resolve it, but wiring it in is its own method and gets the
same definition-before-application discipline. Raw roster counts run 12-29
against a 15-max; the dated filter narrows, not fixes, that distortion.

### The gate, honestly

"Admits the reported suitors and still excludes a meaningful share": the second
half holds (22 of 30 excluded), the first is 3 of 6 - including, decisively,
the team that signed him. The three still out share one named, data-basis
cause. **Part-open: the thirty-agent run remains blocked pending the expiry
basis, and proceeding is a decision, not a default.**

Representability re-checked: **34 of 71, unchanged** - its inputs are
prior-season values and standings-based dispositions, neither consumes payroll,
confirmed by rerun rather than assertion. Leakage audit line updated: dating is
no longer the weakest link; contract expiry is.

---

## 38. Expiry validated; the hard filter barely filters in July; the payroll story corrected

### The method held out of sample

3,084 player-slots classified at July-10 dates across six seasons none of which
is 2025-26. EXTENDS 1,251, EXPIRED 875 (3.1-7.1 slots freed per team - exactly
expiring-contract-sized), UNRESOLVED 958, every one occupying by design.

| check | result |
|---|---|
| demonstrably-expired (Y-signing after D) wrongly called EXTENDS | **0 of 908** |
| of those, correctly freed / occupied-safe | 489 / 419 |
| false frees under the one freeing rule | **5 of ~875 (~0.6%)** |
| undateable | 0 |

The conservative shape shows exactly where designed: 419 signed-elsewhere
players occupy their old slot as UNRESOLVED rather than free it on ambiguity.

### The three-stage suitor check

| stage | admits | reported suitors admitted |
|---|---|---|
| 1 raw season table | 6/30 | LAL, MIN |
| 2 + dated presence | 8/30 | LAL, MIN, **PHI** |
| 3 + contract expiry | **24/30** | **GSW**, LAL, MIA, MIN, PHI |

**GSW is admitted (bird, taxpayer MLE, minimum): the branch scenario is
runnable.** CLE alone stays out - 15 occupied slots at $212.0M - and the filter
tests the roster *as it stands*; that a GM could clear a slot by waiving a
non-guaranteed deal is not modelled, and is now the stated reason a reported
suitor can still be excluded.

**And the honest headline: 24/30 means the hard filter barely filters at a
July freeze.** With slots open, almost any team can legally sign a 22-year
veteran to the minimum. The apparent selectivity of stages 1-2 was data
artifact, not signal - legal feasibility discriminates in-season, where rosters
are genuinely full, and the earlier standing rule applies verbatim: a filter
that nominates most of the league is not filtering, and the suitor question
belongs to the soft filter (record) and reported interest.

### The payroll story, corrected (the diagnosis-by-plausibility failure)

Entries 33 and 35 said Philadelphia lost every route because undated contracts
*inflated its payroll* past the aprons. The measured inflation ($126,157,001 /
2.27%) is real. **The binding constraint was never payroll: it was roster
count.** PHI's payroll moved $727,637 under dating; its roster count moved
16 -> 14, and 16 >= 15 blocks every signing route before money is consulted.

The mechanism was checkable the whole time - the solver's blocked-route reasons
say "roster is full" in so many words - and nobody read them, because the
payroll story was plausible and the metric said the right team was excluded.
Same family as a check certifying the wrong surface: **a diagnosis certifying a
plausible mechanism while the actual reasons sat unread in the scan output.**
Audit text in sim/branch.py corrected; entries 33/35 stand as written, wrong
mechanism and all, per the no-rewriting rule - this entry is the correction.

---

## 39. The soft filter does not filter, and that is the finding

Written blind, committed before first run, threshold computed from precedent
rather than chosen — because the author knows the reported suitor list, and any
hand-picked number could have been tuned to it undetectably.

**The measurement:** 43 star-priced veteran signings (10+ years of service,
prior salary >= 20% of that season's cap) across nine offseasons, 3 candidates
dropped for unresolvable service years.

| precedent floor | median | max |
|---|---|---|
| **0.171** | 0.561 | 0.793 |

The floor is the 2024-25 Pistons signing a star-priced veteran off a .171
season. **When the worst team in the sample has done it, no record excludes
anyone**: the soft filter admits 30/30. Per the standing rule it is reported as
not filtering rather than tuned until it looks selective.

| filter | admits | reported suitors excluded |
|---|---|---|
| hard (dated + expiry) | 24/30 | CLE (roster as-it-stands) |
| soft (record precedent) | **30/30** | none |
| both | 24/30 | CLE |

Combined with the hard filter's July result (24/30), the two-round conclusion
is symmetric and real: **in July, neither cap feasibility nor record excludes
teams from star-veteran pursuit.** Rebuilding teams sign star veterans;
near-anyone can afford a minimum. Suitor identification at the moratorium is
not derivable from constraints - it lives in reported interest, which is
evidence, not derivation. Both filters stay, as documentation of that negative.

## 40. The unread output: a new failure family

Entry 38 corrected the payroll story; this entry names what let it live. The
signing solver's scan carries a ``blocked`` map whose values said **"roster is
full"** for Philadelphia the entire time entries 33-35 were attributing the
exclusion to payroll inflation. Nothing was miscomputed, mislabelled or
wrongly scoped. **The diagnostic output existed, was correct, and was not
read.**

That is distinct from the wrong-surface family (a check certifying something
other than its claim) and from scope mismatches (a number valid at one scope
quoted at another). Here every mechanism worked. The failure was narrative:
a plausible story arrived first, the excluded team was the expected one, and
no one consulted the machine's own stated reasons.

**Standing rule.** When a mechanism excludes, rejects or blocks, quote its
stated reason in the report - not a paraphrase, not an inference from which
inputs changed. If the mechanism does not expose a reason, that is a defect in
the mechanism. The expiry method's ``ExpiryCall.reason``, the joins' miss
samples and the solver's ``blocked`` map all exist so this rule is satisfiable;
the failure was not consulting them.

---

## 41. Thirty agents: the competition reshuffles the market and moves no metric

Same branch, same seed (20260731), run sequentially, never concurrent. The
planner is deterministic (the LLM path is measured separately, per the standing
limitation), so both configs with both branches completed in ~13 minutes of
wall-clock with zero LLM calls.

### On the five scored teams

| | 5-team | 30-team |
|---|---|---|
| signing sets changed | — | **4 of 5 teams** |
| proposals (scored five) | 14 | 14 |
| hits | 1 (the stipulated premise) | 1 (the same premise) |
| predictive recall, non-stipulated | 0 of 1 | **0 of 1** |
| ARBITRARY resolutions | 0 | **0** |

**Composition moves, metrics do not.** With 25 competitors added, GSW loses
Caldwell-Pope and DeRozan to the market and takes Paul George and Jerami Grant
instead; MIA and PHI lose three of their signings each to competition and
backfill with cheaper players. Every scored headline is unchanged: the one hit
is LeBron-to-PHI, which is the branch premise in both configurations, and the
one non-stipulated actual arrival (Bassey to GSW) is missed in both.

The null makes the equality unsurprising: 14 proposals against 2 actual
arrivals in a ~520-player pool expects ~0.05 hits by chance, so at this n the
metric cannot separate the configurations - only the composition can, and it
separated clearly.

### Contention under 24 legal bidders

The 30-team blocker branch: **8 contested players from 184 offers**, resolved
5 by "clearly stronger roster" and 3 by "higher offer" - **zero arbitrary**,
against the M5.5 defect where a coin-flip decided contests. The per-event
relevance held without a bolt-on: contenders per player are exactly the teams
`feasible_signings()` gives a route to, which is the hard filter applied
per event rather than globally.

### Cost

| | 5-team | 30-team |
|---|---|---|
| agent wakes (both branches) | 30 | 120 |
| events | 25 | 225 |
| scheduler saving vs polling | — | **98.4%** |
| wall-clock, all runs pooled | ~13 min | (same window) |

Wakes scaled 4x for 6x the teams - sublinear, as the event-driven design
predicts - and the naive-polling baseline it is measured against grew to 3,360.

**Not extracted this round:** the league-wide pooled precision recompute
against its null (the `pooled` key was not read after two key-guessing
misreads earlier in the session; restraint beat another guess). The scored-five
comparison above is complete and is the comparison the experiment was for.

**Not scored, and never will be:** the branch itself is counterfactual. Its
value is comparative, and the comparison says: more agents change who gets
whom, not how well anyone predicts. More agents is a product feature.

---

## 42. The news ingest: typed interest, a corrected count, and three honest scores

Nine `reported_interest` rows, every one **derived from an existing verified
anchor** - dates, sources, URLs copied from the anchor item, so each row
restructures a curated claim rather than adding one. Anchor integrity is
validated at load: an unanchored row is "a new claim wearing a citation" and
fails the ledger.

**Typing corrected a figure immediately.** The suitor set is five (GSW, CLE,
MIA, MIN, PHI - LBJ-04), not six: LAL entered the substring-matched set via
LBJ-01, a *departure* fact. Mention-counting invented a suitor; a typed record
cannot.

**Circularity, enforced.** PRE interest rows are inputs; POST rows (LBJ-06's
narrowing) are gated behind SCORING_UNLOCK like every answer. Suitor
identification is retired as a scored metric, the README says so beside the
branch-premise note, and a test fails if any scored output is computable
without POST access.

**Relevance paths:** the reported path fires for the 2 players interest rows
name (James, Davis); the structural path covers the rest of the ~130-player
pool. Reported never mixes with structural, so the path is attributable per
event.

**The three scored outcomes:**

| outcome | result | null |
|---|---|---|
| suitor_won | sim **GSW**, actual PHI - **miss**. Resolution reason, quoted: *"arbitrary - nothing available separates the offers"* | 1/4 = 25% |
| capacity_use (GSW) | sim proposed Curry Jr./George/Grant; actual re-signed Green **$27,678,571** (verified against the structure snapshot before encoding) and retained Bassey, Horford, Melton, Porzingis, Payton - **0/3 proposed, 0/6 recall** | 0.14 expected hits |
| conditionals_fire | **4/4** attach to the branch matching their condition | 2.0 (random) |

Two findings inside the misses. The suitor resolution came back ARBITRARY with
four contenders - money and roster tier cannot separate them, which is the
same conclusion the project reached about LeBron-at-the-minimum from the
beginning, now produced by the resolver itself and quoted per the unread-output
rule. And the capacity miss is structural, not noise: **re-signing your own
expiring capacity is not in the planner's move set**, so it chased external
stars while the real team retained five of its own. CLE dropped from
contention with its blocked-route map quoted in full.

**The limit:** one scenario. The ranker would need hundreds of dated interest
items across ten seasons, with dating unreliable toward 2016, and would still
lack its named orthogonal features. Recorded in the README in those terms.

---

## 43. The 5/5 vs 0/6 contradiction: two planners, and a pool built from the answer

M4.5 scored Golden State's retentions 5/5; entry 42's capacity_use scored the
same team, offseason and outcomes 0/6. Resolved by reading what each counted
**at the time it was written** - neither definition was adjusted after seeing
the numbers.

**Not a regression. Two different planners.**

* `sim/branch.py` (M4) plans over Golden State's **own free agents** -
  retention *is* its move set. Its per-move score has three parts: `retain`
  (was the player in the plan - a planner check, near-tautological until the
  second-apron ceiling forced drops, as its own output notes), `route` (does
  `check_signing` reproduce the actual route - a **rules** check), `terms`
  (actual salary within the route's maximum, exact where the mechanism
  determines a figure - a rules check). The remembered "5/5" is a retention
  planner scored on retention outcomes, plus the rules reproducing them.
* `sim/league.py` (M5, commit 450b8d5) is **acquisition-only** and was born
  that way: retention never left its move set, because it was never in it.

**The pool consumed the answer.** `free_agent_pool()` = a 2025-26 contract and
no 2026-27 deal *anywhere* - and the 2026-27 table is ground truth. Every
player who actually re-signed is excluded from the sim's market before any
planner runs: of capacity_use's six actuals, five were unreachable and the
recall ceiling was **1/6** (Payton), with 0.02 expected chance hits.

So entry 42's "re-signing your own capacity is not in the planner's move set"
named the wrong mechanism and the wrong scope: true of the league planner via
pool construction, false of the branch planner, which does nothing else. The
metric is renamed `external_acquisition_overlap` - the name now says what is
measured - and its report states the ceiling.

The pool construction itself is a leakage finding, same family as the
`arrivals()` caveat the module already carries: a POST-freeze table shaping sim
inputs, here removing re-signees from everyone's market - depressing
acquisition recall and shielding incumbents from contention at once. The fix
(an expiry-based pool; the validated machinery exists) is a deliberate separate
change, not a patch made while the number is on the table.

**Power labels applied, per the standing rules:**

| metric | label |
|---|---|
| suitor_won | **UNINFORMATIVE** - n=1 against a 25% null, on a choice the resolver itself called arbitrary |
| external_acquisition_overlap | **NO POWER BY CONSTRUCTION** - ceiling 1/6, 0.02 chance hits |
| conditionals_fire | **SUGGESTIVE, NOT SIGNIFICANT** - 4/4, p=0.0625, the threshold refused on the era gap |

*Tooling coda:* four silent text-replace failures this week - a README no-op
the pre-commit hook caught, two anchor no-ops, and an f-string corruption -
trace to one cause: heredoc quoting is not honoured in this shell, so doubled
backslashes halve in transit. Working rule now: boundary-asserted line surgery,
and no backslashes in patch payloads. Nothing broken reached a commit.

## 44. A departure fact was a suitor for three entries

LBJ-01 says LeBron's Lakers tenure is **over**. Substring matching over prose
counted the mention as interest, LAL entered the reported-suitor set, and "six
reported suitors" ran through entries 33, 37 and 38 - every survival fraction
keyed on the set inherited the phantom sixth. Corrected denominators: the
stage-3 suitor check admitted **4 of 5** true reported suitors (not 5 of 6);
CLE remains the one real exclusion. Prior entries stand as written; this entry
is the correction.

**What caught it: imposing a schema on data that had been read loosely.** A
typed `reported_interest` row demands a team and a player per claim, and a
departure fact cannot fill them. Nothing about the correction required new
information - the corpus said "five" all along (LBJ-04 names them); the reader
added the sixth.

---

## 45. The derivation audit: two more leaks, one repair that changes nothing

The leakage audit checked inputs for post-freeze **content**. free_agent_pool()
leaked by **derivation** - a legitimate table computing something only the
future knows - so the audit was re-run for derivation: every sim-side consumer
of the 2026-27 table, each answering *could this be computed at the freeze from
pre-freeze information?*, each with its direction. The surface is now
enumerated by AST in tests/test_derived_facts.py, the writer-test pattern, so
the next derivation is declared or the suite fails.

| derivation | freeze-computable? | direction |
|---|---|---|
| free_agent_pool() | no | **hurts** - excluded every actual re-signee from the market |
| freeze_state() | no | **mixed** - post-freeze *re-signings* sit inside the "freeze" payroll (an arrival needs a team change, so subtraction misses them); the sim's books already contain the capacity use it exists to counterfactualise |
| project_wins() | no | **helps** - contested-resolution roster tiers are built from 2026-27 rosters, i.e. outcome rosters |
| arrivals() | no | cleaning + eval target |
| expiring_pool() | yes | repair |
| rights() | yes (2025-26 back) | none |

**The enumeration found freeze_state() and project_wins() before any metric
did.** The helping one matters most: entry 41's contested resolutions ("5 by
clearly stronger roster, zero arbitrary") rested partly on tiers computed from
outcome rosters. The 5-vs-30 *composition* conclusion survives - it never
depended on resolution quality - but the resolution-quality claim is weakened
and is hereby caveated.

### The repair changed nothing, and that is the finding

expiring_pool(), written and committed blind, produces a pool **identical** to
the leaky one on this scenario: 130 = 130, zero new members. The four unleaked
July re-signings stay EXTENDS by the conservative rules; Bassey's leaked row
yields EXPIRED but he has no 2025-26 contract row to enter the base from. So
the external_acquisition_overlap ceiling is **1/6 under any sound
freeze-computable pool**, the observed value cannot move, and re-running the
30-team sim with the new pool would reproduce it byte for byte.

**The metric is retired** - moved from SCORED_OUTPUTS to RETIRED_OUTPUTS with
the reason attached. A score whose ceiling is one reachable outcome in six is a
diagnostic, not a measurement.

### Downstream of the pool: which figures move

None. run_branch's market, entry 41's composition figures, interest_score's
null - all inherit the pool, and the pools coincide, so every published figure
stands. The leak's *direction* was real; its realised effect on this scenario's
numbers is nil, which is exactly the kind of statement the enumeration makes
checkable rather than hopeful.

### The deflating-leak pattern, now a rule about inputs

Second confirmed instance after #11's pooled null. The mechanism, stated: **an
error that lowers a result is not audited, because failure reads as a system
problem rather than a measurement problem.** The standing rule extends to
inputs: an input that constrains the sim away from the observed outcome gets
the same scrutiny as one that points at it. In the README beside its sibling.

---

## 46. The prediction held: freeze tiers, and ARBITRARY rises from zero

project_wins() said "freeze roster" and read the 2026-27 outcome table. The
repair - tiers from dated presence + validated expiry at the freeze - was
committed with a **pre-registered prediction**: at a July freeze nobody has
signed yet, teams should look more alike than their outcome rosters do, and
ARBITRARY should fire *more* than the recorded zero.

| resolution reason | outcome tiers (entry 41) | freeze tiers |
|---|---|---|
| clearly stronger roster | 5 | **1** |
| higher offer | 3 | 4 |
| **arbitrary** | **0** | **3** |
| contested total | 8 | 8 |

**The prediction held.** And the leak's direction is now visible in the
winners: with outcome tiers, Golden State - whose outcome roster contained all
its real retentions - won five contests by "clearly stronger roster". With
freeze tiers it wins one. The help-direction leak was handing the incumbent
its contested wins.

Third independent arrival at one conclusion: the resolver said "nothing
available separates the offers" at entry 42; the suitor filters could not
separate teams at entry 39; and now the repaired tiers cannot either. **Money
and roster tier cannot separate contenders in July.** The contested-accuracy
readout underlines it: 1 of 8 sim winners matches reality, and the one hit was
itself resolved arbitrarily.

Downstream of project_wins (item 3): a single consumer - resolve()'s
projections - so the restated figures are exactly the contested table above
and the signing compositions that cascade from it (three of the five scored
teams' sets shift). Standing untouched: scheduler savings, the zero-LLM cost
finding, suitor_won (it never used projections), the pooled backtest, the
ranker, and every enumeration figure.

## 47. freeze_state()'s direction, quantified: not mixed - hurts, and it flips a tier

The one unresolved "mixed" row in the derivation table, measured. Post-freeze
**re-signings** sit inside the "freeze" books because an arrival requires a
team change and a re-signing makes none.

| | as simmed | corrected (proven set) |
|---|---|---|
| GSW committed at "freeze" | $207,940,722 | **$150,476,177** |
| GSW tier | OVER_CAP | **UNDER_CAP** |
| GSW roster | 11 | 7 |

**Four players, $57,464,545, one team proven** - Green, Horford, Porzingis,
Melton, each anchored to a POST evidence item. Zero further leaked-row
candidates. League-wide unleaked re-signings are undetectable case by case
(the conservative rules call them "continuing deal"), so the proven set is a
floor and GSW is exact only because its POST evidence names every retention.

Direction, per affected figure - not "mixed": entry 41's GSW capacity figures
are **hurt** (committed overstated by $57.5M, the "could no longer afford"
notes fired against phantom commitments, and the scenario's own premise - the
Green opt-out creating room - is erased from the books that are supposed to
model it). The tier flip is qualitative: sim-GSW planned as an over-the-cap
team while true-freeze GSW had cap room.

Repair deferred deliberately, like the pool repair before it: freeze_state is
the next blind-commit candidate, and it is not patched while the number is on
the table. Entry 41's GSW rows stand as written with this entry as their
caveat.

---

## 48. freeze_state repaired: the prediction held to the dollar, and half the movers moved

The rule, committed before it saw the numbers: a 2026-27 row stays in the
freeze books unless a **pre-freeze signal** removes it - a dated post-freeze
signing through the validated expiry rules, or a PRE-evidence option decline
matched on typed subjects plus a declared verbal rule. Never by identity: the
contaminating players were found via POST evidence, and a hand-list from the
answer is the same leak reversed.

**Per player, and by what signal:**

| player | removed? | pre-freeze signal |
|---|---|---|
| Green | **yes** | GSW-01, the June 29 opt-out (PRE) |
| Horford | no | none - stays, floor |
| Porzingis | no | none - stays, floor |
| Melton | no | none - stays, floor |

**Prediction vs outcome:** committed **$180,262,151 - exact**. Tier OVER_CAP -
exact. Roster 10 - exact. Reach Green-only - exact. non_taxpayer_mle opened,
cap_space stayed closed - exact. **Residual floor: 3 players, $29,785,974**,
in the books and stated.

**The movers: half held.** Compositions moved - the freed $27.7M let sim-GSW
add two signings (Post, Spencer) on top of its previous three; the other four
scored teams are unchanged. The contested table did **not** move: same eight
players, same winners, same 1 roster / 4 offer / 3 arbitrary, because GSW's
contest offers were exception-scale under either payroll. Predicting "the
contested table" as a mover was wrong, and the reason it was wrong is itself
informative: the contamination bound GSW's *volume* of moves, not its *bids*.

**Observed residual, noted not chased:** sim-GSW's new signing Quinten Post is
recorded by POST evidence (GSW-18) as under contract with Memphis - a pool
coverage gap where the structure snapshot lacks a row the evidence has.

The input-circularity mirror (no sim input computable only with POST access)
passed across all seven input packages on its first run, and the derived-facts
registry now shows two repairs, one hurts-leak retired with its metric, and
one cleaning entry - with freeze_state the last "mixed" resolved.

---

## 49. The audit closes: gap 0 of 130, a residual corrected, the table resolved

### The coverage gap, measured

Every POST item whose subjects intersect the free-agent pool, judged in the
open: **one** - Payton, "expected back on a veteran-minimum", which is a
report of intent, not a contract. **Zero of 130 pool players are placed under
contract by POST evidence. The structure snapshot is not systematically
incomplete**, and the pool is not inflated.

Which corrects entry 48's residual: Quinten Post's Memphis row **exists** -
that is exactly why he is not in the pool. He reached sim-GSW's option set
through the `all_added` union in run_branch, which pours every team's real
arrivals into the shared market **by design**, so competitors can contest
them. Sim-GSW bidding on a player Memphis actually signed is the contention
design working, not a snapshot hole. The residual was real; its mechanism was
misattributed, and saying so costs one paragraph.

### The derivation audit, closed

| derivation | resolution |
|---|---|
| free_agent_pool() | hurts-leak; repair coincided with it; **metric retired with its 1/6 ceiling** |
| project_wins() | helps-leak; **repaired under a registered prediction - held** (ARBITRARY 0 to 3) |
| freeze_state() | hurts, quantified; **repaired under a registered prediction - held to the dollar**; residual floor **3 players, $29,785,974**, stated |
| arrivals() | cleaning + eval target + market-union, declared |
| rights() | freeze-computable, no leak |
| expiring_pool() | repair |

Every sim-side derivation from an outcome table is enumerated, directed, and
either repaired, retired, declared, or floored in dollars. No "mixed" remains.
Both repairs ran under predictions registered in their commits before
execution, and both predictions held - one exactly, one with an informative
half-miss. The two rounds of leak-closing produced no better headline number
anywhere, and that is the point: they produced numbers whose meaning is now
enumerable.

---

## 49. Assisted curation and the scenario gate: what generalised, named exactly

### The enumeration first: 89 occurrences, 14 files

Freeze dates, subject ids, branch names, scored-team tuples, evidence paths -
the LeBron scenario was a constellation of constants. Now: one declared object
(configs/branch/lebron-2026.yaml) carrying id, season, freeze **with its own
stated rationale**, subjects, decision, branches and scored teams; evidence
keyed by scenario id under evidence/<id>/; a loader with **no default**; and a
fence test that fails on any identifier outside a scenario file or the declared
SCENARIO_DEBT - eight named modules, tracked, with a stale-debt check so a
paid-off module must leave the list.

### The second-scenario gate: NOT passed, and here is the exact boundary

davis-2026 - does Davis end up with Golden State? - was declared and curated
to standard (typed, dated, sourced, anchored, PRE/POST from its own freeze;
outcome verified against the contract snapshot: WAS, $58,456,566).

| layer | ran with zero code changes? |
|---|---|
| scenario load + validation | **yes** |
| evidence ledger + partition + POST gating | **yes** |
| surface (known-at-freeze, provenance, input marker) | **yes** |
| **branch simulation + scoring** | **no - blocked by the eight debt modules** |

The README claims exactly that split and does not claim "any NBA scenario".

### RSS ingest: real timestamps, and the limit demonstrating itself

| feed | items | dated | oldest | in scope of the freeze |
|---|---|---|---|---|
| espn-nba | 12 | 12 | 2026-07-31 | **0** |
| yahoo-nba | 50 | 50 | 2026-07-31 | **0** |
| nba-com | excluded | - | - | HTTP 404, reported not patched |

Every retained item carries a real publication timestamp - the thing the
partition needs and a screenshot destroys. And the in-scope column is the
stated limit made concrete: the feeds reach back about two days, the freeze
was 27 days ago, so zero items reach it. **RSS generalises forward across
current and future scenarios, not backwards** - historical news to 2016, what
the ranker's missing features would need, is not reachable this way.

### The confirmation gate

Drafts land in evidence/<id>/review-queue.csv with the source sentence quoted;
the store has **exactly one writer**, it raises without an explicit human
confirmed=True, phase is computed from the scenario's freeze rather than
declared by the row, and a test greps the package for any other write path.
The phantom sixth suitor, made mechanical.


## 50. The cascade scored conditionally - and the corpus says no (2026-08-02)

**Number:** n = 2 scoreable fallback cases from 20 first-choice-gone events;
0/2 hits against a conditional null of 0.25 on the 1 informative case.
**What was asked:** conditional on a team's rumored first choice being gone
at its ACTUAL pick, does the actual selection match the priority list's next
available name? The condition is evaluated on the real draft's own walk;
using the sim's reconstructed (lottery-free) order would score a contingency
that reality never faced.
**The null is not the unconditional null:** once the first choice is gone
the candidate set is the team's remaining un-taken targets (1/len(remaining)),
and a case whose actual pick lies outside that set is uninformative by
construction - chance scores zero there too, the external_acquisition
ceiling shape again.
**Verdict:** 18 of 20 conditional events exhausted their lists because the
corpus is mostly single-target teams. The measurement needs ranked lists 3-4
deep per team (~120-150 rows); at n=2 the rate is a diagnostic and the
correct move is to say what corpus would support it and stop - which this
entry does.


## 51. A disclosed artifact is not an enumerated one (2026-08-03)

**What happened:** closing the stipulation-integrity invariant, the report
named one violation (BOS signing Giannis). The enumerated invariant test then
found a second (PHI signing Grimes in the curry world). The third - HOU
signing Towns, in a run whose output had been read twice - appeared only in
the diff of the pre-fix and post-fix runs. Disclosure had found one; the
test found two; the diff found three.

**The lesson:** an artifact you noticed and disclosed is a sample of the
violations, not the set. Only two moves produce the set: an enumerated
assertion (every stipulated yaml, every mover, by glob) and a diff of the
affected runs. They answer different questions - the assertion proves the
rule holds NOW; the diff shows what the violation was DOING, and it is where
the third case surfaced.

**Standing rule:** when closing an invariant, diff the affected runs as well
as asserting the rule. Report what the diff shows beside what the assertion
proves, and treat any violation count quoted before both exist as a lower
bound.

**Follow-through, enumerated rather than remembered:** a marker scan of every
git-tracked file (scenario ids + reaction-output markers) found ZERO
committed artifacts generated from a pre-fix stipulated run: runs/ is
gitignored (both local manifests were regenerated post-fix; a programmatic
check confirms no stipulated mover appears in any signed list), the README
quotes only validator findings (pool-independent), and docs/example-run.html
plus the bench-league json artifacts derive from the PENDING path, which the
fix does not touch (stipulated= defaults empty; the lebron numbers are
asserted unchanged by the suite). Nothing needed regeneration; the pre-fix
outputs survive only as quoted history in commit messages, which is where a
violation record belongs.


## 52. Revealed disposition: habits persist, trade construction does not (2026-08-03)

**Number:** out of sample (fit 2016-22, predict 2022-25 under the same GM,
n=11), 4 of 7 derived GM parameters beat the league-average null
(trade_rate, deadline_share, posture_agreement, spend_level) and 3 do not
(aggregation_rate, pick_flow, retention_rate).
**The finding said plainly:** knowing which GM it is beats knowing nothing
for volume, timing and spending habits - and does NOT for how trades are
constructed. The one parameter wired into decision logic (aggregation ->
max_assets_out) is among the failures, so the mapping refuses it without an
explicit probe flag.
**The wiring probe (labelled, not a claim):** forcing the failed parameter
through anyway, 8 of ~9 generated trades differ by identity while 0 of 121
signings do - the pipe reaches the decision logic, so the null result above
is about the parameter, not the plumbing.
**Consistent with:** M10's persona-citation decline with capability - if a
GM's own history barely predicts their trade construction, a text persona
asserting it predicts even less.
**Coverage honesty:** 30 sourced tenure rows, no predecessors on the sourced
page -> 132/300 team-seasons attributable, 8/30 teams UNKNOWN at the live
freeze, all reported rather than defaulted silently.


## 53. The persistence passes were not passes (2026-08-03)

**Number:** under a one-sided sign test at n=11, no GM-profile parameter
clears the threshold this project refused p=0.064 at: spend_level p=0.113
(8/11), trade_rate p=0.274 (7/11), deadline_share p=0.726, posture p=0.500.
**What was overturned:** entry #52 and the README listed four parameters as
'beats the null' on mean-error comparison alone. The same standard applied
to the era gap and the draft was not applied here until asked - the
recurring failure mode where a favourable comparison ships without its p.
**What changed:** validate() now prints the sign-test p per row; verdicts
are three-way (beats / SUGGESTIVE / does not); the README table carries p
and calls the survivors SUGGESTIVE. Power costed like the draft corpus:
n=23 same-GM pairs separates a true 75% persistence rate from chance at
power 0.80; 11 exist; the route to more is predecessor-tenure curation for
the 168 unattributable team-seasons.
**Unchanged:** the refusal gate. Parameters that failed even the mean-error
comparison still may not enter the sim, and the suggestive ones enter only
with their label.


## 54. Suggestive dispositions reach outcomes - prediction registered, held (2026-08-03)

**Registered (commit 68c0ddd, before running):** wiring spend_level to a
first-apron signing ceiling and trade_rate to a cascade gate would produce a
NON-EMPTY identity diff, direction derived <= uniform, signings diff of
order 5-20 concentrated on low-spend teams with possible downstream contest
flips, and fewer generated trades attributable to gated teams.
**Observed:** signings 108 vs 121, identity diff 17 (15/17 on low-spend
teams); the downstream flips happened - OKC and HOU, first-apron-capped,
lose LeBron and Morant to MIN and NYK; generated trades 7 vs 9, identity
diff 10, trade-rate gate kills 11 vs 0, targets rerouting from gated GSW/MIA
to ungated MIN/NYK/PHX.
**What it does and does not establish:** the hooks work - dispositions
wired to real decision points change the world in the stated direction. It
does NOT establish that the dispositions are real: both parameters are
SUGGESTIVE at n=11 (entry #53), so the derived arm stays a labelled
demonstration until the persistence question has its n=23 pairs.
**Contrast with entry #52's probe:** that run confirmed the plumbing with a
null-failed parameter; this one confirms direction and locus with the
suggestive ones, under a prediction that could have failed and was written
down first.


## 55. The handover test: the person does not carry the parameters (2026-08-03)

**Registered (ebb73cc, before running):** nothing clears p<=0.05;
spend_level FLAT; if anything shifts, trade_rate.
**Observed (n=16 clean handovers):** primary held - nothing clears.
deadline_share 2/16, trade_rate 3/16, aggregation 4/16, pick_flow 4/16
(p>=0.98 - decisively flat), retention 5/13. Both specific calls missed
informatively: trade_rate was the flattest, and spend_level leaned SHIFT
(11/16, p=0.105) against its registered FLAT.
**The combined read across entries #53-#55:** n=11 suggestion -> dissolved
at n=29 (no within-stint persistence, spend dead even at mae 0.078 vs
0.077) -> handover-flat on five of six. These parameters are
franchise-condition summaries; the code no longer calls any of them a GM
disposition, and the persona label became franchise-history:<team>.
spend_level is the residue: no within-stint persistence plus a handover
lean is mandate-shaped, not person-shaped - unresolved at this n, confound
stated.
**Design note recorded:** flat survives the mandate confound (a mandate
inflates shifts); a shift result never rises above person-or-mandate.


## 56. M10 restated: the label denoted nothing (2026-08-03)

The original M10 entry stands unedited, per the no-rewriting rule. Its
reading - personas may be text a weak model recites rather than a
disposition it acts from - now has a stronger successor: **the model was
reciting a label that did not denote a disposition at all.** Two
measurements: within-stint persistence, n=29 same-GM pairs, no parameter
beats the league-average null (entry #53 costing, resolved at 2057ca5);
handover shifts, n=16 clean successions, five of six computable parameters
FLAT (entry #55). The citation-rate observation survives untouched - 78.3%
/ 67.7% / 61.2% / 50.6%, falling monotonically with capability - and its
meaning is restated: it measures how readily each model repeats a supplied
label. That is a fact about the models, and nothing about GMs.

## 57. The persona-assertion sweep (2026-08-03)

The rename covered parameters that were measured and failed; this entry is
the enumeration - every remaining place the system attributes behaviour to
a person or a front office's character, and whether it is supported:

- agents/gm.py GMPersona fields and prompt templates: WERE personal-
  disposition framing ("Your decision-making parameters", "your
  asset_hoarding", "this GM will part with"); now franchise-condition
  framing, with PERSONA_FIELD_AUDIT enumerating each field's measurement
  status (asset_hoarding MEASURED-and-failed; risk_tolerance and
  win_now_horizon UNMEASURED and reported as such - the former possibly
  proxyable from posture_agreement, the latter from pick spending, neither
  derived). Prompt rewording post-dates every recorded manifest; recorded
  runs keep their hashes.
- configs/branch/lebron-2026.yaml labels (win-now-veteran, disciplined,
  all-in, star-hunting) and configs/scenario/*.yaml labels: UNSUPPORTED
  character words, but FROZEN measurement inputs - the M10 arms and the
  lebron backtest rendered these exact strings into hashed prompts.
  Reported, not edited: reproducibility of a measured surface outranks
  retro-consistency, and the audit registry documents the supersession.
- mironba/sim/league.py DEFAULT_PERSONA "balanced-default" and its comment
  block: SUPPORTED as written - the comment derives the dials from
  observable cap behaviour at the freeze, a constraint position.
- agents/chat.py: answers attribute to "the persona parameters it was
  given" - provenance framing, SUPPORTED (it claims what was supplied, not
  what anyone is like).
- models/gm_profile.py, models/handover.py, sim/cascade.py,
  sim/league.py signing_ceiling: renamed in the previous round; labels now
  franchise-history:<team>.
- docs/example-run.html and bench-league json artifacts: historical model
  prose and recorded payloads carrying old labels - artifacts of runs,
  kept as recorded, covered by their own limitation blocks.


## 58. GDELT spike: dating guarantee sound, recall UNMEASURED - network-blocked (2026-08-03)

**What was measured before the wall:** one successful DOC 2.0 query
returned 250 articles for the 2026 draft window with ``seendate`` present
on every row, the expected 8-column schema, and mainstream domains
(nbcwashington, forbes, nbcchicago...). The dating guarantee is stated in
the module and stands regardless: seendate is GDELT's own observation
timestamp - an upper bound on publication that works as a CONSERVATIVE
PRE gate (it can under-admit late-seen items, never smuggle POST into
PRE); a backfilled row would gate on seendate and keep the page's
self-reported date for display only.
**What could not be measured:** recall against the 26 draft rows and 9
lebron rows. Every request after the first success returned 429 across
60+ minutes - through a 240s in-run backoff, a 7-minute cooldown, a
45-minute ZERO-REQUEST cooldown, and a User-Agent discriminator (the
exact UA that had succeeded). A first-request 429 after 45 silent
minutes is not a burst penalty; it is consistent with a shared-egress
(CGNAT) neighbour saturating GDELT's per-IP limiter continuously.
**Verdict, claimed as exactly this and nothing more:** INFEASIBLE TO
MEASURE FROM THIS NETWORK TODAY. Recall is UNMEASURED - not poor, not
good - so nothing changes: historical stays hand-curated (the Wayback
conclusion), and the committed spike re-runs any day the API answers,
printing the full two-level recall analysis (exact-source and
claim-level) it was built to produce. No pipeline was built, per the
brief, and none would be until that number exists.


## 59. The wrapper that swallowed the diagnosis (2026-08-03)

**What happened:** gdeltdoc raised ``RateLimitError()`` with an empty
message. The throttling was undiagnosable - no status code, no body, no
retry hint - until the raw DOC API call surfaced GDELT's own answer: HTTP
429 with "Please limit requests to one every 5 seconds", which is what
made the penalty-window behaviour (and the eventual network-level verdict)
readable at all.
**The family this belongs to:** a library that swallows the response
needed to diagnose it is the same failure class as a mechanism whose
success and failure look alike at the call site - the quote-mechanism
lesson (entry 43's unread-output species) wearing a dependency's clothes.
**Standing note:** when a dependency's error path obscures the server's
own answer, drop to the raw call. The committed spike queries the DOC API
directly for exactly this reason.

## 60. The discriminator that wasn't: both alternates shared the egress (2026-08-03)

**What was attempted:** the cheap network test - re-run one GDELT probe
from a different egress. Two in-reach alternates were tried and BOTH were
measured to egress from the same residential IP (136.52.76.203) as the
throttled machine: WebFetch (pinned via api.ipify.org BEFORE interpreting
any GDELT result through it) and an Agent launched with remote isolation
(which executed locally and said so; its own ipify line matched).
**Why the pinning mattered:** without measuring the alternate's IP first,
a 429 through WebFetch would have read as "429 from a second network -
GDELT-wide," a false confirmation manufactured by the assumption that a
different tool is a different network.
**Status:** the discriminator is UNRUN, not failed. From one egress it is
impossible to separate the two live hypotheses - an IP-scoped limiter
saturated by a shared-egress neighbour, versus GDELT-wide load shedding.
A ten-minute run of ``python -m mironba.data.ingest.gdelt_spike`` from a
phone hotspot answers it; until then the route stays PENDING exactly as
entry #58 wrote it, and the local egress constraint is recorded as
network-local-unconfirmed rather than GDELT-wide.


## 61. Two egresses, two causes - and 991 articles lost to a missing writer (2026-08-04)

**The discriminator from entry #60, answered by an operator tether run.**
The run record (evidence/spikes/gdelt-runs.jsonl) now holds both lines:

- home egress 136.52.76.203: 429 on the FIRST request ("draft volume")
  after 45 silent minutes - saturated by another party; nothing this
  project does changes it.
- tether egress 174.195.129.132: four queries answered at 12s spacing -
  the full draft half, 991 articles, three of four at exactly 250 (the
  record cap) - then 429 on the fifth ("lebron window"). That is OUR OWN
  budget: roughly four requests per rolling window, despite the
  documented one-per-5-seconds. The rolling window's length is
  unmeasured; four-at-12s is the only data point.

Different causes, and the verdict changes accordingly: the route is
VIABLE - the limiter is IP-scoped, the home constraint is network-local
(confirmed, closing #60) - and sustained volume is the constraint.

**The lost articles.** The pre-fix spike held results in memory and
discarded all 991 at the fifth query's failure - the incremental-
backtest-writer failure again (entry #49's writer class), in a module
written after that lesson was recorded. Results now persist to disk AS
EACH QUERY RETURNS (append-only, stamped with egress and query label),
and ``--offline`` recomputes recall from persisted batches with zero
network.

**Draft recall today: NOT COMPUTABLE - and not inferred.** Nothing from
the tether run survives on disk, so the offline command reports exactly
that. What IS stated, so the eventual number reads correctly: three of
four draft queries were capped and sorted newest-first, collapsing their
effective coverage to the window's final ~2 days (~June 22-24) - and
every one of the 26 curated rows is dated 2026-05-10..2026-06-18, BEFORE
the collapse point. Exact-source recall from that run would have been
structurally ~0 for capped queries regardless of GDELT's index: absence
there is truncation, not evidence. The offline machinery computes and
prints this per-row (never-fully-searched list) whenever batches exist.
The lebron half stays UNMEASURED.

**Re-costed at the real budget.** maxrecords caps at 250 and the DOC API
has no pagination - coverage extends by window-slicing. Broad NBA volume
saturates a slice in ~2 days (measured once), so a 90-day scenario
window costs ~45 broad requests; per-subject streams run 13-45 requests
each depending on subject heat (7-day slices for quiet names, ~2-day for
a LeBron). A lebron-2026-like backfill (broad + two subjects) is roughly
26-135 requests = 7-34 tether sessions at the measured ~4/session - a
multi-week manual campaign, costed before anything is designed around
it. The cheap wins are near-term: the draft half is ONE 4-query session
(now persisted as it goes); the lebron half is two queries more.


## 62. The absent-writer check: the writer that should have existed (2026-08-04)

**The gap:** the enumerated writer test covers writers that exist - it
proves each declared writer merges/appends correctly. The 991 discarded
GDELT articles were invisible to it because no writer was ever called:
the failure was a module that should have written and did not.

**The complement, now enforced:** any function that acquires data at cost
(network, quota, long runtime) must be declared in its module's
``ACQUIRERS`` map with a discipline - ``persists-per-unit`` (data reaches
disk before the next fallible operation, and the declaration says where)
or ``holds-in-memory`` (permitted only where declared, with the reason).
Discovery is by enumeration over every data/ingest module (names carrying
fetch/query/poll), the same move as the writer registry; declared sets may
exceed discovery, never trail it.

**What the audit found:**
- ``nba_stats.main`` held EVERY fetched season in memory and wrote once at
  the end - a crash at season nine of a throttled ten-season backfill would
  have lost eight. Its writers already merge, so the fix was moving the
  write inside the loop; a test now crashes season B and asserts season A
  survives on disk.
- ``cache.fetch`` - a module the hand audit MISSED and the enumeration
  found - is the per-unit persistence for every bbref page (body + meta
  written inside fetch itself), which is also why build.py needed no fix.
- ``archive.poll`` and ``rss._fetch`` were already per-unit (writes inside
  the feed loop). ``gdelt_spike`` is per-unit since the fix this entry
  generalises.
- The one declared residual: the concluded wayback spike's three probes
  hold in memory - report-only, conclusion recorded at entry #58, re-run
  cheap. A new in-memory holder must appear in that pinned list
  deliberately or the test fails.

**Companion re-scope (items 2-3 of the same brief):** the sliced recall
plan replaces broad windows - 12 queries (2 subject batches x 6 seven-day
slices) covering 2026-05-08..06-19, cost stated before the first request,
per-query persistence, resumable, capped slices flagged rather than
trusted; offline recall now reports the untruncated-coverage denominator
beside the never-fully-searched list. Attempted from home: first-request
429, self-labelled, zero loss. The number awaits ~3 tether sessions.


## 63. The player-level ranker: properly powered, and it works (2026-08-04)

**Reframe:** unit moved from the team pair (~71 positives / 435 candidates,
chronically underpowered, recorded negative untouched) to the player -
n=5,631 (player, deadline) rows over ten deadlines, 353 positives, 6.3%
base rate. n, class balance and both nulls stated before fitting;
missingness reported by class first (largest gap 5.8pts on availability -
below the flag line; the miss-indicators enter the model as columns so any
residual class correlation is absorbed transparently, not silently).

**Result (leave-one-season-out logistic):** mean test AUC 0.650 (train
0.662 - no meaningful overfit gap); mean p@25 12.4% vs 6.3% class-balance
null (1.97x, +6.5% normalized headroom) and vs 7.2% WITHIN-TEAM null
(1.72x) - the null that preserves each team-season's trade count exactly
and therefore absorbs the team_prior_rate feature. Within-team permutation
p<=0.002 in 9/10 folds; 2023-24 (10 positives) p=0.341, underpowered and
said so. Feature importance: log_salary 1.18, availability 0.32, age 0.15,
team_prior_rate 0.12.

**The ablation is the claim's control:** salary+team_rate alone score p@25
6.4% - the base rate, nothing - so the orthogonal features carry the
entire +6.0-point lift. Consistent with the pair negative rather than
against it: solver-consumed features carry no ranking signal; pre-solver
signals do.

**Two discipline notes.** (1) The availability fence was NARROWED, not
removed: eval/player_ranker.py is the one permitted consumer outside the
display surface, enforced by the fence test's allowlist; planner and value
model still may not read it. (2) expiring-contract status is NOT COMPUTABLE
for any season: the only structure snapshot is forward-looking, contracts
that ended pre-retrieval had left the page, and forward-absence inversely
encodes the label through post-deadline outcomes - the season+1 leak. The
test written to pin the feature to 2025-26 is what exposed it; the feature
was dropped with the reason kept greppable in place.


## 64. One leak class, two members - and the clean result that survived (2026-08-04)

**Entry #63's numbers were contaminated and are superseded (kept, not
erased).** The leak class item 4 of the brief asked about - "could this
feature be computed only with post-deadline information?" - had two members
in the new features:

1. **Team assignment.** bbref's contracts row lists a traded player under
   his ACQUIRING team: roster team != last pre-deadline team for 84-91% of
   positives vs 8-12% of negatives (measured on 2024-25 and 2021-22). "Zero
   appearances in his team's window" therefore partially encoded the label.
2. **The label window's own events.** Fixing (1) by cutting features at the
   deadline let January trades - inside the Jan 1..deadline label window -
   write themselves into switched_pre and the team assignment: a player
   traded in January appears for his new team before the deadline. This
   leak INFLATED the result (p@25 24.0%), and switched_pre's coefficient
   collapsed from +0.29 to +0.01 when it closed - the coefficient was the
   leak.

**The fix:** features cut strictly at Jan 1 (the label window's start);
team-entering-January derived from the player's own pre-January
appearances; contracts team only as a flagged fallback. Question restated:
entering January, will this player be traded by the deadline?

**Clean numbers (10 deadlines, leave-one-season-out):** AUC 0.645 (train
0.651), p@25 9.6% vs 6.3% class null (1.53x, +3.5% headroom) and vs 7.2%
within-team null (1.34x); within-team AUC permutation p<=0.018 in 9/10
folds (2023-24: 10 positives, p=0.226, underpowered and said so).
Plainly: ~2.4 of 25 flagged are traded vs ~1.6 at chance and ~1.8 under
the within-team null; a random traded player outscores a random untraded
one 65% of the time.

**Decomposition finding:** window_share -0.43, never_active -0.28,
injured_shaped -0.15, log_salary +1.08 - PAID-BUT-NOT-PLAYING players get
traded: a marginal-rotation effect, not an injury effect (never-appeared
players are LESS traded than low-minute actives). Ablation: salary+team
rate alone score 5.2% - below base rate - so the appearance components
carry the whole +4.4-point lift.

**Age's residual channel, quantified:** 473 of 5,631 rows carry an age
despite zero pre-January appearances - bio presence encodes
appeared-at-some-point-in-season. Bounded, reported, and the age VALUE
itself is birthdate-derived and safe.

**Windows verified per season:** the feature cutoff is Jan 1 <= deadline
for all ten seasons (test), and a synthetic post-cutoff appearance cannot
reach a profile (test).

**The pattern's name:** the correction chain 12.4% -> 24.0% -> 9.6% is why
figures and benches regenerate from recorded sources - each contaminated
number was believed at the time it was written.


## 65. The leak-fix-direction rule (2026-08-04)

**The observation:** closing a leak should LOWER a result - the leak was
donating signal. The ranker's correction chain went 12.4% -> 24.0% -> 9.6%:
the first fix (log-derived teams) RAISED p@25 by 11.6 points, and stopping
there would have reported a doubling as an improvement. The rise was itself
the evidence: the deadline cutoff had opened a second channel (January
trades inside the label window), and the "improvement" was that channel's
donation.

**Standing rule:** when a leak fix raises a number, treat the rise as
evidence of a SECOND leak rather than a better model, and do not report
until the rise is explained. A fix that removes contamination and adds
nothing should move the number toward the null, not away from it.

**Detection signature, recorded beside the rule:** a feature that separates
classes at 84-91% versus 8-12% is not a strong feature - it is the label
wearing a feature's name. Real features in this domain separate classes by
points, not by factors of ten.

**What the existing check could not catch:** missingness-by-class audits
absence. Both leaks here were PRESENT-AND-WRONG values - a team assignment
that existed but was post-hoc, an appearance profile that existed but
included the label window - so the missingness table stayed clean while the
features leaked. The complement check is the one that worked: ask of each
feature "could this value be computed only with information from inside or
after the label window?", per feature, per season - the question that
caught expiring (#63), the team assignment, and the January channel (#64).


## 66. The interaction, tested explicitly - and the main effects migrated (2026-08-04)

**The hypothesis:** salary+team_rate alone score BELOW the base rate yet
log_salary carries the largest coefficient, so salary must be informative
only conditioned on playing time - paid-but-not-playing is an interaction
the additive model reconstructs through its main effects.

**The term:** log_salary x (1 - window_share) x (1 - never_active) -
salary-weighted low minutes among players who appeared before January.

**Result (same folds, same within-team fold null of 7.0%):** p@25 9.6% ->
11.2% (1.37x -> 1.60x the fold null); AUC 0.645 -> 0.647, i.e. flat. The
coefficient migration is the confirmation: window_share's main effect
flips -0.425 -> +0.165 and log_salary shrinks 1.076 -> 0.983 while the
explicit term takes +0.446 - the additive form had been spending its main
effects to imitate the product.

**Disposition:** entry #64's additive model stays the recorded result. The
term sharpens the head of the ranking (p@25) without improving global
separation (AUC), on ten folds with one MC draw - suggestive of a better
form, not yet a replacement. Recorded in bench-player-ranker.json under
interaction_test; anyone re-running --fit reproduces both.


## 67. The interaction settled: intervals, and a promotion under a pre-stated rule (2026-08-04)

**What one draw could not say, 10,000 did.** Entry #66's 1.37x-vs-1.60x
rested on a single Monte Carlo draw of the fold null. The settle run fits
both variants once per fold, then scores BOTH models' heads against the
same 10,000 paired within-team label permutations:

- additive: observed 9.6% vs null 7.02% (sd 1.47%), P(null>=obs)=0.035
- interaction: observed 11.2% vs null 6.97% (sd 1.49%), P=0.0026
- THE DIFFERENCE: observed +1.60% vs null -0.05% (sd 0.55%, 95% interval
  [-1.2%, +1.2%]), P(null>=obs)=0.0044
- fold bootstrap of the observed difference: +1.61% [0.0%, +3.2%];
  interaction wins 5/10 folds, ties 4, loses 1

**Spread checked, per the degenerate-null lesson:** all three nulls carry
healthy variance (sd 0.55-1.49 points); no ratio here divides by a
constant.

**Disposition, by the rule stated before the draws ran** (promote only if
the difference clears P<=0.05): PROMOTED. The interaction model is the
recorded headline - p@25 11.2% (1.78x class null, 1.57x within-team), AUC
0.647, ~2.8 of 25 flagged traded - and the headline matrix now carries
salary_x_low_minutes as a derived column. The additive model's numbers
stay recorded (bench interaction_test + entry #64) as the superseded step;
--fit carries the settle record forward instead of overwriting it, which
its first run did and the re-run repaired.


## 68. The sustainable rate: unmeasurable from home, delegated to a cloud egress (2026-08-04)

**Item 1, measured from home (136.52.76.203):** the ladder - 30s, 60s,
120s, each rung until 429 or 20 consecutive successes - returned 429 on
the FIRST request at every rung (per-rung streaks {30: 0, 60: 0, 120: 0},
every event timestamped in gdelt-rate-probe.jsonl). No spacing is
sustainable from this egress; the saturation is not ours to pace around,
consistent with entry #61's diagnosis. Re-costing at the home rate is
therefore division by zero - the honest cost line is "0 requests/day from
home; every job needs another egress."

**Item 2, alternatives measured not assumed:** no VPN exists on this
machine (no Windows VPN profiles, no installed clients - scanned). The
cloud runner is delegated to .github/workflows/gdelt-probe.yml: it labels
its own egress (ipify before any GDELT request), runs the SAME ladder with
the useful queries first (unpersisted recall slices, then lebron/davis),
persists per query, uploads an artifact even on failure, and commits its
run record and article batches back to the repo - self-reporting, since
this machine has no gh CLI. A sustained rung there completes the recall
measurement as a side effect and dissolves the tether requirement; a 429
is a labelled datacenter-throttling result. Whatever it reports supersedes
nothing until pulled and read.

**The probe queue design note:** successes are never wasted - the ladder
spends its streak on real work first, so "measure the rate" and "collect
the data" are the same requests.


## 69. The egress ledger closed; the archive left the machine (2026-08-05)

**The rate, measured everywhere reachable (items 1-2 of the brief):**

| egress | kind | ladder result |
|---|---|---|
| 136.52.76.203 | home (shared residential) | 429 on the FIRST request at 30s, 60s, 120s - zero sustainable; saturation is not ours to pace around |
| 174.195.129.132 | operator tether | 4 requests @ 12s then 429 (entry #61) - the only egress that has answered |
| 172.184.209.169 | GitHub Actions (Azure) | 429 on the FIRST request at all three rungs - datacenter ranges ARE throttled harder, now measured |

No VPN exists on the operator machine (profile and client scan: none).
The tether requirement does NOT disappear. Re-costing: from home and
cloud the cost is undefined (zero throughput); at the tether's measured
budget the 12-query recall run is ~3 sessions and the 85-135-request
backfill is ~22-34 sessions. The sustainable-spacing question stays open
exactly where it can be answered: the ladder is committed, its queue
leads with the useful queries, and one tether run of --rate-probe
answers spacing AND collects the recall data in the same requests.

**The archive off the logged-in machine (item 3), verified end to end:**
rss-archive.yml polled from the cloud, committed, and the partition
pulled back clean - with one measured surprise en route: a feed served
an EMPTY body to the datacenter IP and the parse error killed the whole
poll, marker included. Both pollers now exclude unparseable bodies the
way they exclude fetch failures (regression-tested), so a bot-blocked
feed costs one feed, not the day. Cloud coverage is therefore PARTIAL
by feed (yahoo answered the runner; espn's body was empty) - which the
per-feed EXCLUDED lines and the __poll__ marker record per run, and the
offset local tasks cover from the residential side. Two writers into
the same partitions are safe via git plus reader-side URL dedup, tested
against a simulated merge duplicate.

**Tooling notes for the ledger:** GitHub job logs 302 to pre-signed blob
storage that rejects a forwarded auth header - follow the redirect bare;
and mironba.data's __init__ imports yaml, so even stdlib-only submodules
need PyYAML installed in CI.


## 70. Two writers, one record: the local task stopped forking the archive (2026-08-05)

**The trace (reported before any change):** the local scheduled tasks ran
`python -m mironba.data.ingest.archive` with NO pull before and NO commit
after - appending to the working tree and exiting. The task itself never
errored; divergence accumulated silently and surfaced later as a hard
`git pull` refusal on whoever next synced. Until that manual sync, the
local coverage report answered for a fork of the record.

**The decision:** the archive of record is THE REPO at origin/main. Both
writers commit into it; neither side is a mirror.

**The fix, only what the trace showed missing:** scripts/local-archive.cmd
pulls (--rebase --autostash) BEFORE polling so the union sees cloud rows,
polls, commits only archive/rss (through the pre-commit suite), pulls
again for races, pushes. Same-day two-writer appends resolve via the
committed merge=union attribute on archive/rss/*.csv - correct precisely
because the files are append-only and readers dedupe by URL (the
merge-artifact test). A failed push leaves the commit local for the next
run to carry.

**Verified the way the cloud loop was:** a REAL fork was forced
deliberately - cloud poll dispatched (committed 4feee3e), local poll run
without pulling (the old pattern, dirtying the same partition) - then the
new script ran over it. Both sides survived: five poll markers in the
day's partition, ZERO duplicate URLs even before reader dedup (the fork
resolved at the autostash pop through the union driver), linear history,
push clean. The offline twin lives in the suite: a scratch repo with two
branches appending to one partition, merged under the attribute, read
back clean.

**The coverage report answers for a named copy now:** every health line
carries "[reading the LOCAL working copy of the record (archive/rss);
in sync with origin/main (as of last fetch)]" - or the exact divergence:
uncommitted partition files, unpushed commits, unpulled record commits.
During the forced fork it read "1 partition file(s) with uncommitted
rows; 1 record commit(s) not pulled" - the wrong-surface failure family,
made visible instead of possible.


## 71. The A/B that measured nothing, and the speed-up that broke extraction (2026-08-05)

**The invalid A/B, recorded as a mistake.** ``bench-authoring-latency.json``
first carried two arms labelled thinking_off and thinking_on. They were the
same configuration: ``client.complete()`` takes the profile PER CALL, and
``draft_from_sentence`` passed its own - so the client's profile was
ignored in both arms. The 345s-vs-165s gap the "A/B" showed is arm ORDER
(the first arm pays the cold model load), not thinking. A test would not
have caught this; reading the call site did. Pattern: a control that varies
a setting the code path overrides measures only its own ordering.

**The real comparison, run afterwards on one sentence, same code path,
profile the only difference** ("Victor Wembanyama traded to the Warriors"):

| | thinking OFF | thinking ON |
|---|---|---|
| step 1 | 4s - teams=[], moves=[] | 133s - teams=[GSW], moves=[] |
| step 2 | 3s - moves=[] | 60s - one move extracted |
| outcome | dead end | 8 solver packages offered |

**Thinking off is fast and useless on this model**: it emits minimal valid
JSON instead of extracting. The authoring role is therefore thinking ON,
and the honest UI copy says the wait is the price of a usable draft -
p50 ~4.3 min, warm ~2.8 min, worst 8 min over 7 recorded drafts.
``authoring_nothink`` stays declared as the arm that was measured and
rejected.

**The two-step is NECESSARY, not vestigial.** With thinking ON, step 1
still returned ``moves=[]`` - the charter's documented nested-one-shot
failure, live. Step 2's tiny movements-only schema is what produced the
move. Extracting it for per-step UI progress did not collapse it (tested);
the regression the operator saw was the thinking-off profile, not the
schema change.

**Incidental confirmation:** the model returned ``to_team="Warriors"`` - a
nickname - and the new team resolver mapped it to GSW. The resolver earned
its keep on its first live sentence.

## #72 — the 404 was a stale process, not a route

`/live` returned 404 in the browser while `TestClient` returned 200 for the
same path in the same working tree. The route was never wrong: PID 23660 was
still LISTENING on 8300, left over from an earlier background smoke test, and
it was serving the code it had started with — from before `/live` existed.

A server holds the code it booted with. Editing a file does not change what an
already-running process serves, so a 404 from a long-lived dev server is
evidence about *that process's* age, not about the route table. The check that
settles it costs one command: compare the in-process route list
(`[r.path for r in app.routes]`) against what the port returns.

`api/serve.py` now probes the port before binding and refuses to start behind
another listener, printing the reason and the command to clear it. The failure
it prevents is not a crash — it is reading a stale page and believing it.

**Streaming, measured.** Drafting `Victor Wembanyama traded to the Warriors`
end to end through the job endpoint: POST returned in under a second, the first
step landed at 0s, structure extraction finished at 88s, and the finished panel
offered 8 solver packages. Previously the same request held one HTTP connection
open for its full duration with no output. The work takes the same time; what
changed is that its progress is now observable while it runs. The measured
distribution (p50 3.2 min, worst 8 min) is printed in the watcher header so the
elapsed counter has a scale to be read against.

## #73 — a rules finding nothing consumed, and the $12,671,000 it cost

`validate_trade` emits findings. The reaction read none of them. That is not
a missing feature — it is the system stating a constraint in one module and
forgetting it in the next, and it produced a wrong number that nothing
failed on.

**The bug, measured.** In `curry-lakers-2026` the validator returns
`HARD_CAP [LAL] hard_cap=$209,015,000` — the seed trade hard-caps the Lakers
at the first apron. The reaction then signed LAL to a committed payroll of
**$221,686,000**, over that cap by **$12,671,000**. The figure is not
approximately the second apron; it *is* the second apron, to the dollar,
because `signing_ceiling()` returned a constant and no finding could reach
it. The manifest carried both numbers on the same page and neither the run
nor any test objected.

**The enumeration.** `Rule` has 18 members. Classifying them by the severity
they are actually constructed with (AST scan, not memory) separates them
cleanly: 13 are ERROR or UNDETERMINED, which means the trade is refused and
`sim/stipulated.py` exits before a reaction exists — those are consumed by
refusing to run at all. Four can accompany a *legal* trade and therefore
reach a reaction: HARD_CAP and ROSTER_MINIMUM (both WARNING, both now
consumed), MIN_TEAM_SALARY (WARNING, ignored — the floor is settled by a
season-end shortfall payment, not an offseason signing), and
MINIMUM_SALARY_EXCEPTION (INFO, ignored — the reaction's own routes
enumerate it independently). The eighteenth, TPE_PRIOR_YEAR, is declared on
`Rule` and constructed nowhere: traded player exceptions are not modelled,
so there is no finding to consume. It is listed rather than deleted, because
a constant that means nothing is worth saying so about.

`sim/obligations.py` holds the table and `undeclared_rules()` returns the
members with no declared disposition; the test asserting it is empty is the
same fence as the writer registry and DERIVED_FACTS.

**Teams forced, per scenario.** curry-lakers-2026: 2 (LAL hard-capped, GSW
one player short of the roster minimum). giannis-knicks-2026: 2 (MIA short
one, NYK short two). The two pending-decision scenarios have no stipulated
seed trade, so no seed findings exist and nothing is forced — zero by
construction, not by measurement.

**After the fix.** LAL ends at $209,015,000 against a $209,015,000 cap —
within, to the dollar. Every roster shortfall is discharged: GSW +1, MIA +1,
NYK +2, all via the minimum route.

**Two things the first wiring got wrong, both caught by looking at output.**

*Gating an obligation on a budget.* The first version filtered obligation
signings through `ceilings[team]` and GSW reported ROSTER_MINIMUM UNMET.
That was false. `signing_ceiling()`'s own docstring calls its return "the
budget a team plans signings against" — it is *behavioural*, derived from
measured spending. An obligation is not a plan. A team does not decline a
roster rule because it already spent to its habitual ceiling. What binds an
obligation is a hard cap, which is law rather than habit; that one is
enforced and the behavioural ceiling is not.

*Paying for a roster spot with an exception.* The second version took the
cheapest route by first-year salary and filled a NYK spot with the taxpayer
mid-level at $440,750 — genuinely cheaper than the $2,449,421 minimum, and
legal. It also hard-caps the team at the second apron for the rest of the
league year, a season-long constraint the reaction does not track. Spending
one to satisfy a roster rule is a real cost incurred invisibly, so routes
that trigger a hard cap now sort last.

**The fence, restated.** The UI now starts runs — as a subprocess, never an
import. The claim that this preserves rather than evades the import fence is
checked in the only way that settles it: a clean interpreter imports the UI,
posts to `/runs/start`, and reports which `mironba.sim` modules loaded. The
answer is none. The first attempt asserted this in-process and failed
immediately, correctly: the test suite imports `mironba.sim` itself, so an
in-process check could only ever have passed for the wrong reason.

**Measured, for the watcher's scale.** A stipulated run start-to-manifest is
about 6 seconds — fully deterministic, no model call anywhere. The whole arc
(confirm write → start → stream → land on the chain-reaction view) was timed
end to end at ~6s.

## #74 — the classifier never classified, and two layers disagreed about free agency

A stipulated seed can now be a SIGNING as well as a trade. Three things had
to be measured rather than assumed, and all three came back against the
first implementation.

**The classifier answered "trade" on every sentence, including explicit
signings.** Adding `event: Literal["trade","signing"]` to the existing
proposal schema and describing it carefully produced this, on the first four
sentences tried:

| sentence | truth | answered |
|---|---|---|
| Stephen Curry traded from GSW to LAL for Reaves and Grimes | trade | trade |
| **LeBron James signs with the Golden State Warriors** | **signing** | **trade** |
| Victor Wembanyama traded to the Warriors | trade | trade |
| Kevin Durant leaves Phoenix for the Knicks | ambiguous | trade |

One of two unambiguous sentences correct — exactly what always answering
"trade" scores. The model was not classifying; it was falling through to the
field default. `moves` came back empty on three of the four as well, which is
the same under-filling that `complete_moves` already exists to repair, and it
identifies the cause: the model drops fields from a schema this size rather
than answering them badly.

The charter's rule for this is to shrink the schema, not to prompt harder.
`classify_event()` asks the question alone, with one field and nothing else
to fill in. On the two decisive sentences it answers **signing** and
**trade** correctly, where the combined schema answered trade for both.

**The full measurement, both arms, same 12 sentences.** `CLASSIFIER_SET` is
balanced six-and-six, so always answering "trade" scores exactly 6 of 12 by
construction and anything at that line has said nothing.

| arm | correct | said "signing" | median latency |
|---|---|---|---|
| null — always "trade" | 6 / 12 | 0 | — |
| A: field inside the full proposal schema | **6 / 12** | **0 of 6** | 175.9s |
| B: dedicated one-field call | **12 / 12** | 6 of 6 | **49.0s** |

Arm A is the null. Not close to it — identical to it, and for the reason the
"said signing" column shows: across twelve sentences it never emitted the
minority class once. A field that can only return one of its two values is
not a classifier, and no accuracy number alone would have exposed that. Arm B
is 12 of 12; under a binomial null at p=0.5 that is P = 0.5^12 = 0.00024.

The dedicated call is also **3.6x faster** — 49s against 176s median. Both
arms run the same model on the same machine; the difference is how much the
model is asked to emit. The charter's "keep schemas small" rule was written
as a defence against schema drift, and this is the first measurement showing
it buys latency and accuracy at the same time, from the same cause.

**What this does not establish.** The twelve sentences use unambiguous verbs
— signs, joins, agrees to terms, inks against traded, deals, swaps,
acquires. It measures whether the model maps a clear verb to the right
label, which is the easy half. The genuinely ambiguous case ("Kevin Durant
leaves Phoenix for the Knicks", which could be either) is not in the set and
has no ground truth to be scored against; the flow's answer there is to let
the snapshot overrule the classification, which is why a signing for a player
under contract is refused and told to say "traded" instead.

**Two layers disagreed about who is a free agent.** The runner's signable
pool is `free_agent_pool() | (arrivals - pre_freeze)` — 264 players. The
authoring gate used `contracts_2627` alone — and refused LeBron James,
Giannis Antetokounmpo, Paul George and Jaylen Brown, who are exactly the
players worth stipulating. They hold 2026-27 contracts signed in July,
*after* the 2026-07-06 freeze. Refusing on the row alone uses post-freeze
information to rule out a counterfactual set at the freeze: the same leak
class as the ranker corrections, arriving through a gate rather than a
feature.

The fix aligns authoring with the runner's own definition — on this team in
the target season and NOT on it in the prior one. The first attempt at that
read prior-season rows from `bbref-contracts-2026-27/contract_years.csv`,
which contains 2026-27 onward and nothing earlier, so every lookup returned
empty, every player looked like an arrival, and **Stephen Curry passed as a
free agent**. The prior season lives in `bbref-2025-26/contracts.csv`, which
is the file `LeagueState.load` was reading all along.

**A scenario can hide its own null.** `run_branch` removes `SUBJECT` from the
signable pool. The first signing scenario named its signee as
`decision_subject`, copying the trade scenarios — so the signee was removed
from the run WITHOUT the seed too. The null could not contest him, "who else
wanted him" returned nobody, and the diff reported that the seed changed
nothing. Every one of those readings is false and none looks like an error.
`build_signing` now refuses the configuration by name.

Corrected, the same scenario reads: **18 teams made a legal offer for LeBron
James in the unseeded run; OKC won him there.** With the seed he goes to
Golden State, OKC signs Paul George instead, that costs LAL Paul George, and
LAL signs Khris Middleton — which costs Dallas Middleton. Three teams signed
differently in all (OKC, LAL, DAL — Dallas never bid for James), and two
contested players went elsewhere, one on a higher offer and one on an
arbitrary tiebreak, counted separately as always.

Two of the eighteen pursuers acted differently. The other sixteen offered and
lost in both runs, and the page says so: "18 teams wanted him" without that
split is precisely the kind of number this project exists to refuse.

**What the signing path is judged by.** Not `trade_validator.py` — a signing
has no counterparty and no salary matching, so it has nothing to say.
`rules/signing.py` enumerates the routes and `rules/signing_solver.py` owns
the roster-full answer. Golden State has four legal routes for him; the
Lakers have none, blocked by a 16-player roster rather than by money, and the
refusal quotes that rather than returning an empty list. No schema field
carries an amount, so the runner takes the best legal route and records that
the figure was **derived, not declared**.

## #75 — how many fields does each call ask for, and what did the classifier
## actually emit

Two rules fall out of #74, and both are now structural rather than
remembered.

### Every model call's field count, derived

`mironba/llm/schema_audit.py` enumerates all ten call sites that pass a
schema. Counts are **read out of the source**, never declared:

| purpose | fields | nested | total | disposition |
|---|---|---|---|---|
| scenario_draft | 8 | 3 | 11 | candidate |
| curation_draft | 6 | 0 | 6 | candidate |
| trade_intent | 5 | 0 | 5 | candidate |
| trade_intent_retry | 5 | 0 | 5 | candidate |
| scenario_draft_moves | 1 | 3 | 4 | measured |
| action_choice | 2 | 0 | 2 | by-design |
| package_selection | 2 | 0 | 2 | by-design |
| report | 2 | 0 | 2 | by-design |
| event_classification | 1 | 0 | 1 | measured |
| agent_chat | 1 | 0 | 1 | by-design |

The first draft of this registry declared the counts by hand and got two of
ten wrong — `TradeIntent` as four fields when it has five, `BranchSummary` as
four when it has two. A registry whose numbers are typed in drifts from the
code it describes, which is the failure it exists to prevent. They are AST-
derived now, and the schemas are searched package-wide rather than at the
call site, because a call and its schema usually live in different files —
looking only where the call is returned **zero for four of ten entries**, and
zero is a plausible-looking number.

Which is the same trap in miniature: "schema not found" and "schema with no
fields" are both `0` and call for opposite responses. `field_counts` now
raises `SchemaNotFound` instead of returning a number. It was caught because
the registry named the RSS curation schema `CurationDraft`; it is called
`Draft`, colliding with the authoring dataclass.

**The queue is visible, not implied.** Four multi-field calls are declared
CANDIDATE — unmeasured, not endorsed. Splitting them all on the strength of
#74 would be generalising from n=1, the same error facing the other way.
Splitting one more field out of `scenario_draft` costs +1 round trip, ~49s
measured, on a p50 of 3.2 minutes: about 25% more wall clock per draft.

### Accuracy never travels alone

`eval/classifier_score.py` returns the predicted class distribution with the
accuracy, in one function, so a caller cannot print half of it. Applied to
the recorded arms of #74:

```
arm A - field inside the full proposal schema: 6/12 = 50.0% (null 50.0%)
  predicted: trade x12
  truth:     signing x6, trade x6
  DEGENERATE: never predicted signing - this is a constant, not a weak
  classifier; the accuracy above is uninformative about those classes and
  prompting will not move it

arm B - dedicated one-field call: 12/12 = 100.0% (null 50.0%)
  predicted: signing x6, trade x6
  P(>= this many correct | null) = 0.00024
```

A degenerate predictor and a mediocre one score identically against a
balanced null and need opposite responses — a structural change versus a
better model — and accuracy alone cannot separate them. The test suite pins
the distinction with two fixtures that score the same and are classified
differently.

**Third instance of one failure shape:** a mechanism whose success and
failure look alike at the call site. The others were a null with degenerate
variance, where a ratio divided by something with no spread (#29), and a
weighting that silently collapsed to uniform so the weighted and unweighted
answers were the same number. In each the headline was computable, plausible
and uninformative, and only a statistic nobody had asked for separated
working from broken.

### The charter now carries the evidence

"Keep schemas small" was a precaution against drift. It now states 6/12 inert
versus 12/12, and 176s versus 49s, so a reader can tell a measured rule from
a cautious one — with the explicit warning not to split every multi-field
call on principle. "No accuracy without its predicted distribution" is a new
non-negotiable, alongside "no metric without its null".

## #76 — the graph is a view of a manifest, not a property of two demos

The league graph rendered on one page, for whichever recorded run happened to
be newest. A scenario authored a minute ago therefore had no graph unless it
won that race. `run_graph(manifest, run_id)` draws from a manifest directly,
so the graph is embedded on every run view, linked full width at
`/runs/{id}/league`, and present the moment a UI-started run finishes.

**The thin cases are the point.** A new scenario can produce few edges, no
generated trades, or a cascade that stops at depth zero. All three look
identical to a broken renderer - thirty nodes and nothing joining them - so
each is named rather than left to be inferred. Verified output:

| case | edges drawn | what the figure says |
|---|---|---|
| no generated trades | seed only | "NO GENERATED TRADES … 7 candidate pairs killed by the counterparty gate and 3 by the solver … that result, not a missing layer" |
| cascade at depth zero | seed only | "TERMINATED AT DEPTH ZERO. Nothing woke a second round" |
| no contested players | seed only | "NO CONTESTED PLAYERS CHANGED HANDS. Either no player drew offers from more than one team, or every contest was won by a team that lost none" |
| nothing at all | 0 | "NO EDGES AT ALL. The N node(s) below are the league at the freeze … Nothing connected them, and that is the run's result" |

The notes stack: a run with none of the three says all of it. And they must
NOT fire on a healthy run, or they are decoration - `curry-lakers-2026`
carries zero notes, pinned by test.

That last note first read "the thirty nodes below". Wrong for any run whose
reaction covers fewer teams, and wrong in exactly the way a hardcoded count
always is - invisibly, on the case nobody looked at. It counts now.

**The real signing run is a thin case already.** `lebron-warriors-2026` draws
103 edges and still carries two notes: **no trade is attributable to the
seed** (all ten generated trades happen in the unseeded run too), and **no
seed edge** (a signing has only a destination, so it colours a node rather
than drawing a line). A reader looking at 103 edges without those two
sentences would take the whole picture for a consequence of the stipulation.
It is not.

**The caption is the fix for the same misreading.** Every graph now states
the run, the edge counts split by type, and how many generated trades are
attributable by the unseeded diff. `curry-lakers-2026`: 107 edges - 3 seed,
9 generated trade, 95 contested-player - of which **4 of the 9 generated
trades are attributable**, against a null of 10 in the unseeded run. Ninety-
five of those edges are the market resolving and are explicitly not claimed
as consequences.

The graph also comes under the figure-null rule now, on all three pages that
draw it. It was outside that rule only because it was not a `<figure>`.

**Kept unchanged, deliberately:** disposition read off the record with
`unclassified` where the artifact does not say (a run where every team was
classified would mean the UI guessed - pinned by test), fixed geographic
positions rather than a force layout (two calls must place a team
identically or two runs cannot be compared - also pinned), and edges
animating in recorded order with the seed at order -1, ahead of everything
it caused.

## #77 — the candidate was measured and it says DO NOT SPLIT

The one candidate, measured the way #74 was: `kind`, the other `Literal`
classifier in the same eight-field `Proposal` schema that `event` was inert
inside. `KIND_SET` is six stipulated sentences and six pending-decision ones,
declared before the run so the set could not be chosen to suit the answer,
which puts the majority-class null at 6/12 by construction.

| arm | correct | predicted | median latency |
|---|---|---|---|
| null — always "stipulated" | 6 / 12 | — | — |
| `kind` inside the full schema | **12 / 12** | stipulated x6, pending_decision x6 | 79.5s |
| dedicated one-field call | **12 / 12** | stipulated x6, pending_decision x6 | 17.5s |

**The field is not inert.** It is perfect inside the large schema, emits both
classes evenly, and the dedicated call is indistinguishable from it on
accuracy. Splitting would cost +1 round trip — ~49s, about 25% more wall
clock per authored scenario — and buy nothing measurable. `classify_kind`
stays in the tree as the measured-and-rejected arm, the way
`authoring_nothink` is kept in `models.yaml`.

**This is the more useful half of #74.** `event` and `kind` sit in the same
schema, are the same shape — a two-value `Literal` with a described field —
and one was a constant while the other was flawless. So the lesson is not
"large schemas break fields". It is:

> Schema size says where to look, never what you will find.

Had the #74 result been generalised, every multi-field call would have been
split on the strength of n=1, every draft would have cost an extra round
trip, and the change would have been justified by a rule of thumb rather
than by evidence — the exact failure the null discipline exists to prevent,
arriving through a fix rather than a metric.

**The distribution column earned its place again.** Both arms score 12/12
here, and both are non-degenerate. In #74 the accuracy alone was 6/12 for
arm A, which reads as "weak"; only `predicted: trade x12` showed it was a
constant. The rule catches both directions: it names a degenerate predictor,
and it certifies a genuine one. Recorded raw in `bench-kind-arms.json`.

Registry updated: `MEASURED` now explicitly does not mean "split" — one field
in `scenario_draft` was split on measurement and another was kept on it.

## #78 — watching the fields instead of studying them, and what that found

#77 removed the shortcut: schema size predicts nothing, so soundly deciding
each label field would mean a 12-sentence labelled study per field. But
**degeneracy needs no ground truth**. Every `runs/*/llm_calls.jsonl` already
records what the model emitted; `llm/degeneracy.py` reads them and counts.
The watch list is AST-derived from the `Literal` annotations, never declared,
because a hand-maintained list rots invisibly - a field dropped from it
simply stops being watched.

**Current record, across every recorded run:**

| field | allowed | emitted | n | runs |
|---|---|---|---|---|
| `Proposal.kind` | stipulated, pending_decision | stipulated x62, pending_decision x6 | 68 | 35 |
| `EventKind.event` | trade, signing | trade x7, signing x7 | 14 | 2 |
| `ScenarioKind.kind` | stipulated, pending_decision | stipulated x6, pending_decision x6 | 12 | 1 |
| `Proposal.event` | trade, signing | — | 0 | 0 |

Nothing is flagged. Two things turned up anyway.

**One raw response fell outside its Literal.** `Proposal.kind` was emitted as
`"trade"` once - not one of its two allowed values, and plainly the model
confusing it with the `event` field beside it. That is the pre-validation raw
text, so pydantic and the repair retry stood between it and anything
downstream; what it shows is that guided decoding did **not** constrain the
field on that call (`schema_sent_to_server: true`,
`schema_enforcement_observed: null`). Counted separately from the class
distribution, because a value outside the enum is evidence about enforcement,
not a class the field legitimately uses.

**Omission is the sharper signal, and it is the #74 signature.** A field that
is *asked for and never returned* is answered entirely by its pydantic
default whatever the input said - and unlike single-valuedness, that
conclusion does not depend on the inputs having varied. `Proposal.event` is
exactly this. The scan could not say so at first: the log recorded the schema
NAME but not its fields, so 69 historical `Proposal` calls that predate the
`event` field were indistinguishable from 69 omissions. Reporting "69
omitted" would have been a fabricated finding about calls that never asked.
`client.py` now logs `schema_fields` per call, and the scan counts omissions
only among calls that requested the field.

Validated live: one fresh draft, `asked=1, omitted=1`, and the resulting
`Draft.event` is `"trade"` - the default, for a sentence reading "LeBron
James signs with the Golden State Warriors". **The monitor reproduces #74's
finding from one call with no labels at all**, and correctly refuses to
conclude anything at n=1.

**The floor is n=12, and it is not a round number chosen for comfort.**
During #77, `kind` had emitted nothing but "stipulated" through the first six
sentences - the set runs stipulated-first - and looked exactly like a
degenerate field. It finished 12/12. Six observations of one value proved
nothing because the first six inputs were all of one kind, and any field
whose inputs have not varied is in the same position.

**What this does not replace, stated in the module, in the committed JSON,
and in the charter.** It finds inert fields. It cannot tell a correct field
from a wrong one: a field emitting both values in healthy proportion may be
emitting them for the wrong sentences every time, and there are no labels in
a run record to notice. Accuracy still needs a labelled set and a study with
its own null. What changed is that one specific failure - the constant
wearing a classifier's type signature - no longer requires one.
