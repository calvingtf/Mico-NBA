# The standing boundary

The standing boundary: what the model is allowed to see and do, and how each limit is enforced.

[← back to the README](../README.md)

---

## The standing boundary

What remains open, and for each why it is BLOCKED (no route exists with the
data this project can reach) or COSTED (a route exists and its price is
stated). This list is the honest project boundary; everything above it is
either measured, retired with a recorded reason, or listed in "still
unmeasured" as free-to-run work that has not been run.

- **Draft corpus** - COSTED: ~120-150 hand-curated ranked interest rows
  (lists 3-4 deep per team) buy ~20-25 informative conditional cases, enough
  to separate a 50% fallback rate from the 1/4 null (entry #50). Anything
  less buys another diagnostic.
- **Historical news** - BLOCKED: RSS reaches back ~3 measured days, the
  archive reaches back to its first partition (2026-07-31), and the Wayback
  CDX spike came back 0/26 - the source articles were never captured at all.
  A GDELT route exists and is COSTED in its own
  entry below (entries #58-#61). Historical scenarios stay
  hand-curated; UNRECOVERABLE-BY-RSS gaps stay declared. The standing
  archive is the only source of future PRE evidence.
- **GDELT backfill** - COSTED: the route is VIABLE and slow - the
  constraint is sustained volume on a throttled egress, not access. The
  dating guarantee is sound (seendate is a third-party existed-by timestamp,
  a conservative PRE gate), the limiter is IP-scoped (tether run answered
  where home could not), and the measured budget is ~4 requests per rolling
  window. A full scenario window (broad + subject streams) is ~85-135
  requests = 7-34 tether sessions - a multi-week manual campaign
  (subjects-only floor ~26 requests). The recall measurement is cheap by
  comparison: the sliced draft plan is 12 queries ≈ 3 tether sessions,
  persisted per query and resumable (`--recall-run` skips already-persisted
  slices); the LeBron half adds its own slices and stays UNMEASURED until
  they run. Attempted from home 2026-08-04: 429 on the first slice,
  self-labelled, zero loss (run record on disk). The egress ledger is now
  closed (entry #69): home sustains ZERO at any probed spacing (30/60/120s
  ladders, first-request 429s), GitHub Actions' Azure egress is throttled
  identically (measured, not assumed), and no VPN exists on the machine —
  the tether remains the only egress that has ever answered. The committed
  `--rate-probe` ladder spends its streak on the recall queries, so one
  tether session measures the sustainable spacing and collects the recall
  data in the same requests.
- **Market model resolution** - BLOCKED at current inputs: the value model's
  win-delta error is 10.48 wins, so contention tiers separate only extremes,
  and single-star trades can flatten to zero projected-win shift (reported
  as produced in every stipulated run). Sharper resolution needs a better
  player-value input, not more simulation.
- **Multi-team trade generation** - COSTED: the validator handles 3-team
  legality (M9), but the PROPOSER still generates two-team packages plus a
  near-miss absorber; free-form k-team generation is unbuilt and its search
  space grows combinatorially. No measurement claims cover it.
- **Follow-on trades in the reaction** - BUILT for two-team trades
  (`sim/cascade.py`); k-team generation stays COSTED (combinatorial search,
  no measurement claims). Intent is proposed deterministically - a cost
  decision stated in the module, not a capability claim (the LLM tick runs
  ~30s/call; thirty teams across rounds would turn a 13-minute reaction into
  hours) - and the solver and validator are unchanged, with the LLM path
  intact for the intent A/B. Triggers are the scheduler's existing events
  (contest losses wake teams; executed trades emit TRADED events that wake
  only interested teams - no polling). Acceptance is the existing
  standings-based disposition gate: a counterparty parts with a player only
  as a SELLER; no value-based acceptance is modelled because the 10.48-win
  resolution cannot support one (still BLOCKED, below). Termination is
  declared before running: one executed trade per team, one attempt per
  team, depth cap 3. Stipulation integrity extends to generated trades
  under the same enumerated glob test. **The headline is the null diff, not
  the raw count**: each scenario runs twice, with and without the seed, at
  the same date and seed - giannis-knicks: 9 seeded vs 10 unseeded
  generated trades, **4 attributable to the seed**, 5 displaced (the
  unseeded world trades around Giannis as a market free agent; the seeded
  one cannot); curry-lakers: 9 vs 10, **4 attributable** (the seed's
  fingerprint is package composition on the teams it touched - Warriors
  ship Porzingis+Green instead of Butler once Reaves and Grimes arrive).
  Counterparty gate killed ~385 candidate pairs per run; depth reached 1 of
  3, so the depth cap did not bind. UNFALSIFIABLE in output and manifest,
  like the rest of the stipulated path: a demonstration, and the diff is
  what makes it an honest one.
- **Pick valuation** - COSTED: pick assets validate in trades, but no value
  curve is fitted, so a pick-heavy package cannot be compared to a
  player-heavy one; the published-curve comparison from the M9 brief is the
  stated route. Rookie-scale cap effects remain NOT_MODELLED (draft v0
  scope statement).

### What the persona evidence now supports (boundary statement)

Mechanism: CONFIRMED - dispositions wired to real hooks change the world in
the registered direction (entry #54). Persistence: NULL at proper power -
n=29 within-stint pairs, no parameter beats the league average, spend_level
dead even (entry #53 costing, resolved by 2057ca5). Person-attribution:
NONE ESTABLISHED - five of six computable parameters are handover-flat and
are named franchise-condition summaries in the code; spend_level leans
shift at handovers (p=0.105), which is mandate-consistent and unresolved;
posture_agreement remains the lone SUGGESTIVE within-stint survivor
(p=0.105 at n=16) and reaches no decision logic. The uniform arm therefore
remains the defaults; every derived arm is a labelled demonstration.
