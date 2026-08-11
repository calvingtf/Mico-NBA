# Walkthrough

A guided pass through the system: the canonical scenario, the authoring flow, and what a run produces.

[← back to the README](../README.md)

---

## See it

**The web UI** — presentation only; it reads committed artifacts and a test
fences it from ever computing (no imports from `sim/`, `models/` or
`eval/`):

```bash
python -m mironba.api.serve        # http://127.0.0.1:8300, Ctrl+C stops everything
```

Nine screens. A landing view leads with the boundary finding and an
**animated hero built from a real run's cascade** (SVG + CSS, no library);
a **league graph** draws thirty team nodes on a fixed geographic layout with
the run's own trade and contested-player edges animating in the order they
occurred — node size is payroll, colour is disposition *as the record proves
it* (the cascade's counterparty gate admits only SELLER teams, so a recorded
counterparty was seller-classified at run time; a team that did not
participate is labelled **unclassified**, because the artifact does not say
and the page does not guess), and hovering a node shows its payroll bar with
the cap/tax/apron lines marked. A **live run view** polls a run directory as
the CLI writes it — elapsed and event count measured, not estimated. The
branch page is drawn as an actual **fork**: shared trunk, split at the
decision, both limbs rendered, conditionals marked fired-or-not. Numbers on
the results page **animate from their null to their observed value**, because
the travel is the finding. Then: scenario input (the CLI authoring flow with its confirm gate
intact — writing without the explicit checkbox is a 400; an under-specified
sentence like *"Wembanyama traded to the Warriors"* is the normal case, so
the destination's legal return packages are enumerated by `rules/solver.py`
and offered as a choice rather than refused), the run gallery
with every manifest (model, seed, snapshot, gpu_fraction, reproducible),
run timelines with refusals leading and validator reasons quoted verbatim,
branch comparison with the counterfactual headlined UNFALSIFIABLE and
conditionals shown fired-or-not per branch, the report agent's recorded
output with its undismissable limitations block, and the generated figures
each captioned with its null. Every page footer carries the label: one
model per tick in the intent loop — not thirty agents.


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

**What the timeline's granularity actually is:** there is no in-world clock
anywhere in the system. A GM-tick run logs 11-15 events across 8-11 distinct
kinds; the timestamps are wall-clock capture times of the generating process
(21-30 seconds end to end on the local model, ~7 minutes hosted), and event
ORDER is the only temporal structure. The 30-team reaction persists no event
log at all - scheduler counters only (119-137 events per run), with no
timestamps. Nothing maps any event to a simulated hour or day; a reader
should treat the feed as sequence-ordered news with provenance, not as a
calendar.

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

### The canonical scenario: a demonstration, not a measurement

The project's founding example - *Stephen Curry traded to the Lakers* - is a
different shape from the backtests above: nothing is pending and nothing
forks. The event is **stipulated** and the only question is what follows.

```bash
python -m mironba.sim.stipulated --scenario curry-lakers-2026
```

Three things about that run, in order of importance:

1. **The stipulated trade goes through `rules/` before anything else.** The
   scenario file declares only who moves where; salaries and payrolls are
   derived from the contract snapshot, and the package must pass
   `rules/trade_validator.py`. The first two packages tried failed apron
   matching (a team over the first apron matches at 100%, and Curry earns
   $62.6M); the declared package - Reaves and Grimes for Curry - is LEGAL,
   with the validator's own findings printed (the Lakers come out
   hard-capped at the first apron). If a stipulation is illegal the runner
   prints why and exits; it never bypasses the rules to make a premise
   happen.
2. **The output is labelled UNFALSIFIABLE, in that word.** There is no world
   where this trade occurred, so there is no ground truth, no score, and no
   null - and unlike everywhere else in this README, none is stated, because
   there is nothing to compare against. What survives is provenance: dated
   inputs, a deterministic seed, a manifest. The run is reproducible even
   though it is not checkable. **It is a demonstration, not a measurement.**
3. **Thirty teams react through the same machinery the backtests exercise** -
   the market loop, the solver, the contention model. That machinery's error
   rates are the measured ones above; the demonstration inherits them and
   adds an unmeasurable premise on top. Read its output as "what this
   system does with the premise", never as "what would have happened".

---
