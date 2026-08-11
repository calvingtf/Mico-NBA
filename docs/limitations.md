# Limitations and unmeasured ground

What this does not do, what remains unmeasured, and how to read a verdict correctly.

[← back to the README](../README.md)

---

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

### Draft simulation v0: rumor-driven, and it loses to the mock - on purpose

One draft (2026), assignment only, scored against exact ground truth with two
nulls. `python -m mironba.sim.draft` walks the 60 slots in reconstructed
order - standings worst-first (**no lottery model**), plus the 9 pick trades
the transaction text attributes unambiguously; 56/60 slots attributed, the 4
unattributable ones listed with reasons, never guessed. Each owning team
takes its highest-priority available rumored target from 26 dated,
source-quoted `draft_interest` rows (11 teams, 17 prospects, hand-curated
because RSS reaches back two days). Contested prospects resolve by pick
order, which is exact. 6 slots resolve; 54 emit UNRESOLVED with the reason;
the first choice was already gone at 16 slots - that cascade is the one thing
the walk contributes over reading the rumor list.

`python -m mironba.eval.draft_score`: **accuracy 1/6** on resolved slots
(unresolved count printed beside every number). Null 1, random assignment of
the named prospects onto the same slots: 0.18 expected hits - the sim is
above it, which is expected and means little. Null 2, a published final mock
(HoopsHQ, dated 2026-06-22, pre-draft) on the same slots: the mock scores
2/5 where the sim scores 1/5 - the sim loses to the consensus mock. Losing
there is **a corpus limit, not a method verdict**: beating a consensus mock
with rumors alone is unlikely by construction, because the mock uses the
same rumors plus the scouting the sim deliberately does not have.

The sim's one unique output is the **cascade** - a mock gives a point
prediction and cannot say what a team does when its target is taken. Scored
conditionally on the ACTUAL draft walk (not the reconstructed order, whose
lottery-free slots are an artifact): 20 first-choice-gone events happened in
reality, 18 of which had no remaining rumored target - single-target teams
cannot cascade - leaving **n = 2 scoreable fallback cases, stated before any
rate**. Fallbacks hit 0/2 against a conditional null of 0.25 expected on the
1 informative case (the other actual pick lay outside the remaining set, so
chance scores zero there too - uninformative by construction). A rate on two
cases is a diagnostic, not a measurement, the same standard that retired
suitor_won at n=1. **The corpus cannot support this measurement, and the
verdict stands as measurements entry #50** - the corpus is deliberately not
expanded. The future decision is costed rather than open-ended: curating
~120-150 ranked rows (lists 3-4 deep per team, shaped like the Warriors'
ten) buys ~20-25 informative conditional cases, which is enough to separate
a 50% fallback rate from the 1/4 null at conventional power - and anything
less buys another diagnostic, not a measurement. Mock rows are `draft_projection`
evidence - a competing forecaster, never an input - and a fence test fails if
any module outside `eval/` can reach them (it caught its own docstring on the
first run). Rookie-scale cap effects are NOT_MODELLED in v0.

## What is still unmeasured

Named because an absent measurement that nobody names reads as a measurement
that came back fine.

- **Whether the persona does anything - now restated (entry #56).** The
  original reading was that the persona may be text a weak model recites
  rather than a disposition it acts from. The stronger explanation, available
  since the GM-profile measurements: the model was reciting a label that did
  not denote a disposition at all - at n=29 within-stint pairs no parameter
  beats the league-average null, and at n=16 handovers five of six computable
  parameters are FLAT. The citation-rate observation itself stands - Qwen
  78.3%, Sonnet 67.7%, Haiku 61.2%, Opus 50.6%, falling monotonically with
  capability - but what it measures is **how readily each model repeats a
  supplied label, which says something about the models and nothing about
  GMs**. The permutation control (permute parameters, hold everything else)
  remains unrun and would now measure label-sensitivity, not
  disposition-fidelity.
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

### GM personas: revealed disposition, not a belief model

`models/gm_profile.py` derives per-GM parameters from transaction history -
and it is named for what it is: **revealed disposition from behaviour. It
says what a front office has done, not what it thinks.** Limit 1 as
originally posed - whether the persona models anything a GM believes - stays
open by choice: a belief model would need the permutation control below to
even be checkable, and behaviour is what ten seasons of transactions can
actually testify about.

The tenure table is sourced or absent, never recalled: 30 rows from one
sourced page (retrieved 2026-08-03), which carries no predecessors - so
**132/300 team-seasons are attributable and 168 are unattributable**,
reported per team (four 2026 hires attribute nothing). A profile is a
function of (team, as_of): parameters compute from seasons strictly before
the date, within the sourced tenure, registered in DERIVED_FACTS; under
`MIN_SEASONS` the profile is UNKNOWN and falls back to league average,
loudly (8 of 30 teams at the current freeze).

**Out of sample, three designs, the story told once.** At n=11 (current
GMs only) four parameters looked persistent. Curating 25 sourced predecessor
stints (coverage 132/300 -> 255/300 team-seasons; still incomplete: MIN 6
missing, CLE 5, CHI/DET/SAC 4 - undated stints stay out) raised the
within-stint design to **n=29 same-GM pairs, past the costed n=23 target -
and the persistence result dissolved rather than confirmed**:

| parameter | n | wins | p (sign test) | verdict |
|---|---|---|---|---|
| spend_level | 29 | 16/29 | 0.356 | does not beat the null (mae 0.078 vs 0.077) |
| trade_rate | 29 | 15/29 | 0.500 | does not beat the null |
| pick_flow | 29 | 14/29 | 0.644 | does not beat the null |
| aggregation_rate | 29 | 12/29 | 0.868 | does not beat the null |
| deadline_share | 29 | 11/29 | 0.932 | does not beat the null |
| retention_rate | 29 | 11/29 | 0.932 | does not beat the null |
| posture_agreement | 16 | 11/16 | 0.105 | SUGGESTIVE, still above 0.064 |

![Persona persistence: the n=11 suggestion dissolves at n=29](docs/figures/persistence-power.svg)

**The handover test** is the design that separates person from franchise -
same-GM persistence predicts the team's future from the team's past either
way; a GM change does not. n = 16 clean lead-to-lead handovers (LAC and NYK
excluded by their sourced tenure notes: the authority did not change
cleanly). Prediction registered in commit ebb73cc before running: primary,
nothing clears p<=0.05; spend_level specifically FLAT; secondary, if
anything shifts it is trade_rate.

| parameter | n | shifted | p | reading |
|---|---|---|---|---|
| deadline_share | 16 | 2/16 | 1.000 | FLAT - franchise-or-noise, not a GM disposition |
| trade_rate | 16 | 3/16 | 0.998 | FLAT - franchise-or-noise, not a GM disposition |
| aggregation_rate | 16 | 4/16 | 0.989 | FLAT - franchise-or-noise, not a GM disposition |
| pick_flow | 16 | 4/16 | 0.989 | FLAT - franchise-or-noise, not a GM disposition |
| retention_rate | 13 | 5/13 | 0.867 | FLAT - franchise-or-noise, not a GM disposition |
| spend_level | 16 | 11/16 | 0.105 | leans shift - not separable from drift at this n |

The primary prediction held; the spend-specific one did not (it leans the
other way), and the secondary hedge on trade_rate was wrong in direction
(3/16 - decisively flat). The five flat parameters are renamed in the code:
they are franchise-condition summaries, and nothing calls them GM
dispositions any more. spend_level is the interesting residue: dead even
within stints yet leaning shift across them is what a MANDATE looks like -
payroll posture resets when regimes change, rather than travelling with a
person.

**The residual confound, stated beside the result:** a handover is not
clean either - new GMs often arrive with a mandate, so a behaviour shift
can be the situation rather than the person. This design narrows the
person-vs-franchise confound; it does not remove it. A FLAT result is the
stronger reading (a mandate would inflate shifts, not suppress them);
spend_level's lean stays person-or-mandate and is labelled so.

**The derived-vs-uniform experiments**, three layers, all reported:

1. *Wiring probe* (aggregation - a null failure - forced through, labelled):
   0 of 121 signings differ but 8 of ~9 generated trades differ by identity.
   The pipe reaches decision logic; the parameter feeding it had still
   failed its null.
2. *Suggestive parameters wired to real hooks* (prediction registered in
   commit 68c0ddd BEFORE running): spend_level -> a first-apron signing
   ceiling for the 13 below-average-spend teams; trade_rate -> a cascade
   gate for the 12 below-average-rate teams; deadline_share NOT WIRABLE (no
   in-world clock) and says so. **The prediction held in both channels and
   in the predicted direction**: signings 108 vs 121 with an identity diff
   of 17 (predicted 5-20), 15 of 17 on low-spend teams, including the
   predicted downstream contest flips - Oklahoma City and Houston, capped
   at the first apron, can no longer afford LeBron and Morant, who land
   with Minnesota and New York instead; generated trades 7 vs 9 with an
   identity diff of 10 and 11 trade-rate-gate kills (0 in the uniform arm),
   the gated teams' targets rerouting to ungated ones.
3. *The label that survives*: dispositions wired to real hooks DO reach
   outcomes - this is mechanism confirmation, not endorsement. The
   parameters feeding the hooks are SUGGESTIVE at n=11 (nothing cleared
   the 0.064-refused threshold), so the derived arm is a labelled
   demonstration until the persistence result has the n=23 pairs it is
   costed at.

### The boundary finding: rank upstream of the constraints

The pair-level negative and the player-level positive are one result, and
it is the strongest generalizable claim this project has produced:

| unit | features sit... | observed | null | verdict |
| --- | --- | --- | --- | --- |
| team pair | **downstream** of the solver (salary matching, apron tiers, roster slots — inputs the solver already consumed) | p@10 6.0% | 5.01% permutation | 1.20x — inside noise; recorded negative |
| player | **upstream** of the solver (who is paid what, who is actually playing — before any constraint runs) | p@25 11.2% / AUC 0.647 | 7.0%±1.5% within-team / 0.5 | 1.57x, P=0.0026; the +1.6pt interaction gain itself clears its null at P=0.0044 |

Features the constraint solver has already consumed carry no ranking
signal; features that precede it do. The general implication: **in a system
with a hard constraint layer, put the model upstream of the constraints,
not downstream.** Downstream of a solver, the discriminative variance has
been spent — everything surviving is legal-and-similar by construction, and
a ranker there relearns the solver's residue. Upstream, the model sees the
world before the constraints flatten it. Both rows carry their own nulls;
the pair row is 61 trades over ten folds, the player row 353 positives over
ten deadlines with two leak corrections recorded on the way (entry #64) and
its functional form settled by a 10,000-draw paired null (entry #67).

### The convergence

Three independent measurements arrive at one claim: **in this domain the
CBA explains more of what happens than the identity of the person
deciding.**

1. The ranker failed because the constraint solver had already consumed the
   discriminative variance (p@10 6.0% vs a 5.01% null - ranking does not
   work on features the solver has already consumed).
2. Money and roster tier cannot separate contenders in July - the M10
   discriminator result, and the reaction's own resolver declaring contested
   decisions ARBITRARY because nothing available separates the offers.
3. GM behaviour does not travel with the person: at n=29 no profile
   parameter beats the league-average null within a stint, and at n=16
   clean handovers five of six computable parameters are FLAT - the
   franchise's constraint position, not the individual, is what the
   transaction record reveals. (spend_level alone leans shift at
   handovers, p=0.105 - mandate-consistent, unresolved.)

Stated as a convergence of three negatives, not proven as a theorem: each
leg carries its own n and its own null, listed where it was measured.

## Reading a verdict correctly

**`BYC never rejects.`** Base-year compensation emits `UNDETERMINED` and
nothing else — it says the outgoing match value *might* be lower than the cap
hit and that the data cannot settle it. A trade fails later, at salary
matching, if the reduced value is supplied and does not match. So *"BYC
rejected this trade"* describes something the code cannot do, and a finding
that names `BASE_YEAR_COMPENSATION` is a statement about what is unknown, not
about what is illegal.

**`UNDETERMINED` is not a soft rejection.** It is the third verdict, and it
means the question was not answerable on the inputs available. Counting it as
legal inflated the validator's rate to 5 of 5 for a milestone; counting it as
illegal would be equally wrong in the other direction.

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
