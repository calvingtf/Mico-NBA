# MiroNBA

Multi-agent simulation of counterfactual NBA scenarios. See `CLAUDE.md` for the
project charter; this file records what actually exists.

Part of the MiroFish/MiroShark family. Package, charter, and repo folder all
read `MiroNBA`/`mironba`.

## Status: M1.6 — feasibility computed before the model is asked

M1.5 made illegal packages unrepresentable and satisfiability still measured
**0 of 7**. The intents were not malformed; they were unaffordable. The prompt
showed a roster and a payroll and asked what the GM wanted, while withholding
the one quantity that decides what is possible.

M1.6 supplies it — as a set of names, never as a number:

    0. rules/solver.py works out who this team can legally acquire  <- new
    1. choose an action        (LLM)
    2. state a TradeIntent     (LLM)   — targets chosen from that list
       -> rules/solver.py enumerates every LEGAL package for it
    3. pick one by index       (LLM)   — or decline all, with a reason

The model still sees no salary and still emits no package. Both arms are kept
permanently: `--arm blind` is the M1.5 prompt, `--arm feasible` adds the list,
and `bench --ab` runs both.

Measured over 72 trials on a fully GPU-resident model, aggregated across three
scenarios:

| | blind | feasible | delta |
| --- | --- | --- | --- |
| Named an unreachable target | 65.5% | **0.0%** | **−65.5pt** |
| Intent satisfiable, first attempt | 31.0% | **58.6%** | **+27.6pt** |
| Intent satisfiable, final | 58.6% | **75.9%** | **+17.2pt** |

Step 3 ran live for the first time — 39 times, 26 selections and 13 reasoned
declines — and `UNDETERMINED` was reached from a model-generated intent. See
[M1.6 measurements](#m16-what-changed-when-the-model-was-told-what-was-possible).

Two things found on the way there are corrections to what is written below, and
both are marked in place rather than edited away:

- **Ollama 0.32.5 does enforce the schema.** M1.5's headline probe result
  (0/9 conformed) was measured on 0.31.1 and is still true of that version. It
  is not true of the one running now.
- **The solver's search prune was unsound** and silently discarded legal
  packages. It did not change M1.5's satisfiability result, but it did make
  the first M1.6 pre-filter report that the Lakers could acquire nobody at all.

## Status: M1.5 — illegal proposals made unrepresentable

M1 measured the propose-then-validate loop at **0 legal proposals in 12 live
attempts**, with 9 repair retries rescuing none. That result is preserved below
in full, with its manifest, because it is the entire justification for this
architecture and deleting it would leave the design looking like a preference.

The response was not a better prompt. Salary matching is integer constraint
satisfaction, and a language model is the wrong instrument for it. So the model
no longer proposes packages:

    1. choose an action        (LLM)
    2. state a TradeIntent     (LLM)   — what it wants, never a package
       -> rules/solver.py enumerates every LEGAL package (deterministic)
    3. pick one by index       (LLM)   — or decline all, with a reason

An illegal package is now unrepresentable rather than merely discouraged. No
agent-facing schema can pair an outgoing player with an incoming one, and
`test_no_agent_facing_schema_can_express_a_package` enforces that. The
legal-proposal rate is 100% by construction and is deliberately **not** reported
as a finding — see [M1.5 measurements](#m15-what-changed-when-the-model-stopped-proposing).

## Status: M1 — one agent, one tick, first LLM code

M0 is complete and tagged `v0.1.0-m0`; its record is below and unchanged. M1
adds the first LLM code in the project: a single GM agent that proposes one
trade, which `rules/` accepts, rejects, or refuses to decide. No scheduler, no
second agent, no world loop.

```
mironba/
  world/       manifest.py, events.py          ← run provenance, one event log
  llm/         client.py, schemas.py
    providers/ ollama.py, openai_compat.py     ← the only files naming a server
  agents/      base.py, gm.py                  ← structured personas, two-step
  sim/         boundary.py, scenario.py, tick.py, bench.py
configs/       models.yaml, scenario/*.yaml
```

One command runs it end to end:

```bash
python -m mironba.sim.tick --scenario configs/scenario/curry-to-lakers.yaml
```

It prints the proposal, the verdict, the retry if there was one, and the
manifest, and writes every artifact under `runs/<run_id>/`.

See [M1: what the live model actually did](#m1-what-the-live-model-actually-did)
for the measured numbers, including four bugs the measurement exposed
that no unit test would have caught.

## Status: M0 complete — `v0.1.0-m0`

M0 is "no LLM at all": load data into SQLite, and encode 2023-CBA trade
legality as tested Python. The charter's gate is *"Do not write an agent until
this is green."* It is green: every FORMULA row of the
[coverage matrix](#the-m0-gate-branch-coverage) is checked, which is every rule
path whose correctness we can establish without data nobody publishes.

Six REALITY positives are **deferred to M4** rather than closed. They ask a
different question — *did the league actually approve this deal?* — and
answering it needs per-team apron salary on the trade date plus base-year
compensation status. Neither exists in any source we ingest. Deferring them is
not a shortcut around the gate: they are evidence that the rules match
history, and the natural place to collect that evidence is the M4 backtest,
which replays real trades anyway. See
[Deferred to M4](#deferred-to-m4) for the register and what each row waits on.

```
mironba/
  rules/       constants.py, cap.py, trade_validator.py   ← done, tested
  data/        schema.sql, db.py, loader.py, candidates.py
    ingest/    cache.py, bbref.py, build.py               ← real data, cached
    snapshots/ bbref-2023-24, bbref-2024-25, bbref-2025-26
tests/         245 tests, all passing at the v0.1.0-m0 tag
```

At that tag nothing existed in `models/`, `world/`, `llm/`, `agents/`, `sim/`,
`eval/`, or `api/`. M1 has since added `world/`, `llm/`, `agents/` and `sim/`;
`models/`, `eval/` and `api/` remain empty, and deliberately so.

## Run it

```bash
pip install -e ".[dev]"
pytest
```

Every run prints the coverage state in its header:

```
M0 coverage matrix: FORMULA 16/16 GREEN | REALITY 6/12, 6 deferred to M4
```

The deferred count is printed rather than merely recorded because those cells
no longer fail the suite. A number that only appears when someone goes looking
is a number that quietly grows.

On a fresh clone **the tests that need ingested salary data skip**, because
that data is not in the repo — see below. The skip reason carries the command
that rebuilds it. Everything else, including all of `rules/`, runs on a bare
checkout.

## Reproducing the data snapshot

The repo contains the ingest, not its output. To rebuild:

```bash
python -m mironba.data.ingest.build --seasons 2023-24 2024-25 2025-26
python -m mironba.data.candidates --snapshots bbref-2023-24 bbref-2024-25 bbref-2025-26
```

**What it fetches.** 93 pages per run: each of the 30 teams' Basketball-Reference
season pages for three seasons (the per-player `salaries2` table, which the
site serves inside an HTML comment), plus one transaction log per season.

**How long.** About six minutes cold, for three seasons. Almost all of that is
deliberate waiting: `cache.py` holds a 3.5-second floor between requests, well
under the ~20/minute Basketball-Reference asks of automated clients. Responses
are cached to `data_cache/` (~73 MB), so a second run re-parses in seconds and
touches the network zero times. Iterating on the parser costs nothing after the
first pass.

**What you get.** `mironba/data/snapshots/bbref-<season>/` with `contracts.csv`,
`players.csv`, `teams.csv`, `transactions.csv`. A season is written **only if
all 30 team pages and the transaction log parse** — a snapshot missing eight
teams would still load and would still produce apron tiers, wrong ones, so the
failure is loud at ingest time rather than silent at query time.

**Verifying your rebuild matches ours.** `sources.csv` and `snapshot.yaml`
*are* committed, for every snapshot. They record the exact URL and retrieval
date behind each table, so a rebuild can be checked against the run that
produced the figures quoted in this README.
`test_provenance_manifest_outlives_the_data` keeps them from being swept up by
the same gitignore rule that excludes the data. Note that a rebuild today will
not be byte-identical to ours: Basketball-Reference revises historical pages,
and a later retrieval date is the honest record of that, not a bug to paper
over.

### Why the data is not in the repo

Basketball-Reference (Sports Reference LLC) is the source of every salary and
transaction figure here. Nothing in their terms grants redistribution rights,
and much of their underlying data is licensed to them by third parties. So
scraped tables, the derived candidate report, and the raw HTTP cache are all
gitignored. **The snapshots are not redistributed** — you rebuild them from
the source yourself, under whatever terms apply to you.

We could not read the terms text programmatically to quote it: both
<https://www.sports-reference.com/termsofuse.html> and
<https://www.sports-reference.com/data_use.html> return HTTP 403 to automated
requests. Rather than paraphrase language we could not retrieve, we took the
conservative default — absent an explicit grant, assume no redistribution
right — and are recording the gap here in the same spirit as the rest of the
provenance in this project. If you intend to publish anything derived from
these tables, read those pages yourself in a browser first.

This is also why installing this package is not a way to *obtain* NBA salary
data. It is a way to obtain the ingest that fetches it.

`candidates.py` joins salaries to transactions and ranks real trades against
the REALITY matrix rows. It **proposes only**: nothing there writes fixtures or
sets `verified: true`, and `test_candidates_never_auto_promote` keeps it that
way. A candidate is a lead to check by hand. Its output,
`candidate_report.md`, is derived from the scraped tables and is gitignored
with them.

### What the ingested data cannot tell you

Three limits, each pinned by a test rather than left in a comment:

- **`roster_count` is not an active roster.** A season salary table lists
  everyone who drew a cheque; some teams exceed 15 outright. Feed it to the
  roster rules and you get `ROSTER_LIMIT` errors you cannot explain.
- **`re_sign_status` is `unknown` for every player.** Basketball-Reference
  does not publish it, so every trade built from a snapshot comes back
  UNDETERMINED for base-year compensation. That is the correct answer, and it
  is why a REALITY fixture cannot be derived from this data alone.
- **Team salary is a sum of season cap hits**, not apron salary on a date. It
  ignores dead money and cap holds. Any team within $3M of a cap or apron line
  is excluded from tier-dependent candidate rows, which is a floor on the
  error, not a measurement of it.

## What the validator encodes

`validate_trade` returns findings rather than raising — a rejected proposal is
normal simulator traffic, and the *reason* is signal an agent can act on.
ERROR means the league would reject the trade; WARNING means legal but
consequential; UNDETERMINED means we refuse to guess (see below).

| Rule | What it catches |
| --- | --- |
| `SALARY_MATCH` | The three-bracket table, cap-room absorption, and apron matching |
| `AGGREGATION_SECOND_APRON` | Combining outgoing salaries above the second apron |
| `AGGREGATION_WINDOW` | Aggregating a player acquired by trade in the last 60 days |
| `TRADE_RESTRICTION_WINDOW` | Newly signed free agents (90 days / December 15) |
| `NO_TRADE_CLAUSE` | Trading a no-trade-clause player without consent |
| `SIGN_AND_TRADE_APRON` | Acquiring a sign-and-trade player above the first apron |
| `TPE_PRIOR_YEAR` | Apron teams reaching for a stale trade exception |
| `ROSTER_LIMIT` / `ROSTER_MINIMUM` | The 15-player ceiling; the 14-player floor |
| `CASH_LIMIT` / `CASH_MINIMUM` | The annual cash cap; the $110K floor per deal |
| `CASH_SECOND_APRON` | The second-apron ban on *sending* cash |
| `STEPIEN` | Being left without a first-rounder two drafts running |
| `MIN_TEAM_SALARY` / `HARD_CAP` | Falling below the floor; triggering the hard cap |
| `BASE_YEAR_COMPENSATION` | Flags a possible BYC player; returns UNDETERMINED |

Four details worth knowing, because each is a place a plausible-looking
implementation goes quietly wrong:

**Apron matching is season-dependent.** The 2023 CBA capped apron teams at 110%
of outgoing salary in 2023-24, then tightened it to 100% from 2024-25 onward.
Hardcoding one number silently approves trades the league would reject. It
lives in `CapEnvironment.apron_match_pct`, one value per season.

**The bracket table is a median, not a maximum.** The CBA states matching as
three brackets — 200% + $250K for small outgoing salaries, `outgoing +`
expanded-TPE in the middle, 125% + $250K for large ones. The middle bracket
applies only when it falls *between* the other two, which makes the table the
median of the three formulas.

That is not just a tidy reformulation; it is how the brackets are built. The
published edges are *exactly* where adjacent formulas cross, in every case we
can check: 2026-27 publishes $8,846,000 and $35,384,000 against an expanded TPE
of $9,096,000, and both solve exactly. The 2017 CBA's published $6,533,333 and
$19,600,000 do the same against its $5M buffer. Computing the median means the
edges move on their own as the expanded TPE scales with the cap, instead of
being hardcoded and going stale. See `test_published_bracket_edges_are_exact_crossovers`.

**The lower bracket edge is $7.25M, not $7.5M** (2023-24 figures). Sources
disagree; the crossover construction above settles it, and Sports Business
Classroom published a correction to $7.25M after the final CBA text landed.
The full argument, the alternative reading, and what changes if we are wrong
are recorded in `CONTESTED["lower_bracket_boundary"]`. The disagreement only
bites for outgoing salaries in $7,250,001-$7,500,000, and our reading is the
more restrictive one there — errors run toward rejecting a legal trade, never
toward approving an illegal one, which is the safe direction for a gate an LLM
proposes into. Pinned by `test_contested_lower_boundary_at_7_4m`. We did not
read Article VII verbatim; the CBA PDF timed out on fetch.

**The second-apron cash ban is a prohibition, not a limit of zero.** It has its
own rule id and fires on any amount, independent of `cash_limit` — which is a
real number that moves every year and must never be zeroed to express the ban.
Note the ban is on *sending* only; a second-apron team may still receive cash.

### The third verdict

`validate_trade` returns `Verdict.APPROVED`, `REJECTED`, or `UNDETERMINED`.
`TradeValidation.legal` **raises** `VerdictUndetermined` rather than returning
False for the third case, so an undecidable trade cannot be silently absorbed
by `if result.legal:`.

This exists for base-year compensation. Under BYC a re-signed player's outgoing
salary counts for his own team as less than his cap hit, and computing it needs
his prior salary, which rights were used, and the exact expiry window. Rather
than guess, the validator detects the preconditions and refuses to answer. It
is deliberately over-inclusive — expiry is not modelled — and offers two escape
hatches: supply `previous_salary` (a raise of 20% or less rules BYC out) or
`outgoing_match_value` (asserts the caller resolved it).

`ReSignStatus` is tri-state, so a data source that never recorded re-sign
status yields `UNKNOWN` and an undetermined verdict rather than defaulting to
the convenient answer. This has to exist before M4: backtesting real trades
cannot dodge BYC cases the way a hand-picked fixture set can, and scoring them
as rejected would corrupt the hit rate invisibly.

**The minimum salary exception applies in trades, and aprons do not block it.**
A player acquired at or below his minimum does not count as incoming salary for
matching, so a second-apron team sending nothing out can still acquire him.
Hoops Rumors' glossary is explicit: *"The exception also accommodates teams'
acquisitions of minimum-salary players via trade, as players signed via the
minimum salary exception don't count as incoming salary for salary-matching
purposes."* The apron restrictions bar the mid-level and bi-annual exceptions,
not the minimum — the aprons glossary lists the former and never the latter.
Two conditions do apply and are enforced: the contract must run no more than
two years, and the salary must not have exceeded the minimum in an earlier
year of the deal. With years of service unknown we fall back to the
zero-experience minimum, granting the exception only when it holds regardless
of experience.

### Not implemented

The second-apron frozen-pick penalty, poison-pill provisions, two-way and
Exhibit-10 conversion rules, and hard-cap triggers other than the 110% one.

## Data provenance

`rules/constants.py` carries a `PROVENANCE` entry for every field, and a test
fails if a new field arrives without one.

- **verified** — cap, tax, both aprons, apron matching percentage, expanded
  TPE, cash limit, and non-taxpayer MLE, for 2023-24 through 2026-27; plus the
  minimum salary scale by years of service for 2023-24 through 2025-26. There
  is no 2026-27 minimum scale here, so `minimum_salary()` raises for that
  season rather than extrapolating — an invented minimum would silently widen
  the minimum-salary exception and let unmatched salary through.
- **derived** — minimum team salary (90% of cap, cross-checks exactly against
  the published 2026-27 figure).
- **unverified** — none. `test_unverified_constants_are_declared` asserts the
  set is empty, so adding an unsourced number fails the suite.

Two corrections came out of sourcing these:

- `cash_limit` was assumed to track the expanded TPE. It does not. The real
  figures are $7,005,000 / $7,240,000 / $7,964,000 / $8,497,000 for 2023-24
  through 2026-27 — every season lands on 5.15% of that season's cap, which is
  the cross-check. The previous values were wrong by $250K-$600K each.
- `non_taxpayer_mle` for 2026-27 was $15,045,000; the published figure is
  $15,044,000.

The synthetic snapshot that used to live here is gone, replaced by the three
ingested `bbref-*` snapshots. The loader tests now run against real 2024-25
data and spot-check known figures (Curry $55,761,216, Brown $49,205,800) end
to end.

## The M0 gate: branch coverage

The charter's "~30 real trades" was a proxy for the thing that actually
matters — every rule path having at least one verified fixture. Thirty trades
that all exercise the same below-apron bracket prove less than six that hit six
different paths. So the gate is this matrix, not a count.

Rows come in two kinds, because they are answerable by different evidence:

- **FORMULA** — is the calculation right at a specific edge? A *synthetic*
  fixture is not a compromise here, it is the better instrument: it puts the
  inputs exactly on the boundary, which no real trade obliges us by doing.
  Each carries an `edge_justification` showing the arithmetic, and pairs a
  fixture *at* the limit with a counterfactual one dollar past it.
- **REALITY** — does the validator agree with a deal the league actually
  approved? Only a real trade answers this, with `verified: true` and sourced
  cap hits. A synthetic fixture here would be circular: it would test our
  reading of the rule against itself.

The two kinds are gated differently, because only one of them is a safety
property:

- **`test_formula_coverage_is_complete` is the M0 gate** and must be green. A
  FORMULA cell is closeable today by anyone, with no external data — so an
  open one is unfinished work, and it fails the suite.
- **REALITY cells are deferred, not failed.** They are evidence that the rules
  agree with league history. Missing evidence is a real gap, but it is not a
  defect in the arithmetic, and blocking M1 on data that no source publishes
  would stall the project on someone else's publishing decisions.

Deferral is narrow by construction, because "deferred" is exactly the kind of
word a project uses to stop looking at something:

- `test_deferred_reality_cells_are_declared` — an open REALITY cell must be
  named in the register below. Undeclared, it still fails.
- `test_formula_rows_cannot_be_deferred` — closes the trapdoor. Without it,
  moving a failing FORMULA row into the register would turn the gate green
  without the arithmetic ever being checked.
- `test_deferred_register_has_no_stale_entries` — a row that gets closed must
  come out, or the count drifts from reality and stops being read.
- `test_checked_matrix_cells_have_a_verified_fixture` — a checked box must be
  backed by a fixture of the right kind: a FORMULA positive needs a
  `synthetic` one with an edge justification, a REALITY positive a `real` one,
  every negative a `counterfactual`. Otherwise checking a row is typing an x.

| Row id | Kind | Rule path | Positive | Negative |
| --- | --- | --- | --- | --- |
| small-bracket | FORMULA | Below apron, 200% + $250K bracket | [x] `syn-small-bracket-at-limit` | [x] `cf-small-bracket-exceeded` |
| middle-bracket | FORMULA | Below apron, outgoing + expanded TPE | [x] `syn-middle-bracket-at-limit` | [x] `cf-middle-bracket-exceeded` |
| large-bracket | FORMULA | Below apron, 125% + $250K above $29M | [x] `syn-large-bracket-at-limit` | [x] `cf-large-bracket-exceeded` |
| contested-band | FORMULA | Outgoing in $7.25M-$7.50M contested band | [x] `syn-contested-band-at-limit` | [x] `cf-contested-band-boundary` |
| first-apron-matching | FORMULA | First-apron team, 100% matching | [x] `syn-first-apron-at-limit` | [x] `cf-first-apron-exceeded` |
| second-apron-matching | FORMULA | Second-apron team, 100% matching | [x] `syn-second-apron-at-limit` | [x] `cf-second-apron-exceeded` |
| transitional-110 | FORMULA | 2023-24 apron team under the 110% rule | [x] `syn-transitional-110-at-limit` | [x] `cf-transitional-110-exceeded` |
| minimum-salary-exception | FORMULA | Minimum-salary exception acquisition | [x] `syn-minimum-exception-at-limit` | [x] `cf-minimum-exception-one-dollar-over` |
| second-apron-aggregation | REALITY | Second-apron team, aggregation ban | [ ] | [x] `cf-second-apron-aggregation` |
| second-apron-cash | REALITY | Second-apron team, cash prohibition | [ ] | [x] `cf-second-apron-cash` |
| aggregation-below-apron | REALITY | Aggregating multiple outgoing salaries | [ ] | [x] `cf-aggregation-window` |
| tpe-absorption | REALITY | Absorption into an existing trade exception | [ ] | [x] `cf-stale-trade-exception` |
| sign-and-trade | REALITY | Sign-and-trade acquisition | [ ] | [x] `cf-sign-and-trade-into-apron` |
| base-year-compensation | REALITY | BYC detection, undetermined verdict | [ ] | [x] `cf-base-year-compensation` |

### Deferred to M4

Six REALITY positives. Each is parsed out of this section by
`test_deferred_reality_cells_are_declared` — an open REALITY row that is not
listed here fails the suite, and a row listed here that is no longer open also
fails. The register cannot quietly rot.

They are deferred to **M4** specifically, not to "later". M4 is the backtest:
it snapshots world state before a real trade and replays it, so it has to
resolve exactly the two unknowns these rows are blocked on. Verifying them
becomes a by-product of work already planned rather than a separate errand.

Two of those unknowns block every row here, no matter how many candidates the
ingest finds:

1. **True apron salary on the trade date.** Ours is a sum of season cap hits —
   undated, and blind to dead money and cap holds. Teams within $3M of a line
   are excluded from tier-dependent rows, which is a floor on the error, not a
   measurement of it.
2. **Base-year-compensation status** per outgoing player. Not published by
   Basketball-Reference in any form, so every snapshot-derived trade returns
   UNDETERMINED.

| Row | Candidates | Also blocked on |
| --- | --- | --- |
| `aggregation-below-apron` | 37 | — the two above only |
| `tpe-absorption` | 35 | which exception was used; the transaction text never says whether a team used room or a TPE. Needs a per-team exception inventory |
| `second-apron-aggregation` | 8 | — the two above only |
| `second-apron-cash` | 0 | cash amounts. Basketball-Reference writes "cash" with no figure, so neither the limit nor the ban can be checked. Needs a source that publishes considerations by amount |
| `sign-and-trade` | 0 | mechanism labelling. The log records the resulting trade but never the phrase, so the mechanism is invisible in the text |
| `base-year-compensation` | 0 | the precondition itself is unrecorded anywhere |

Candidate counts come from `candidate_report.md`, which `candidates.py`
generates from the ingested data and which is gitignored with it. The three
zero-candidate rows are zero for source reasons, not scarcity — the deals
exist; the evidence that they are the deals is what is missing.

On team payrolls: a fixture states the apron *tier* a team was in, not a
payroll, and the harness synthesises a representative salary inside that band.
Exact payrolls on a date are hard to source and a wrong one produces a
confidently wrong verdict. The tier is what the rule keys on, so this keeps the
test honest about what it asserts. `test_real_trades_clear_the_limit_with_margin`
additionally rejects any real fixture that clears by under $1M, since such a
verdict would be an artefact of a recalled number rather than a property of the
trade.

## What M0 does not settle

The FORMULA gate says the arithmetic is right at every boundary we can
construct. It does not say the validator agrees with the league — that is what
the six deferred rows are for, and until M4 closes them this codebase has no
evidence of agreement with real NBA practice beyond four unverified fixtures.
Worth being blunt about, because "M0 complete" could otherwise be read as a
stronger claim than it is. The rules left unimplemented on purpose are listed
under [Not implemented](#not-implemented).

## M1: what the live model actually did

Measured on this machine against a live local model. Every number below comes
from runs under `runs/`, each with its own manifest; nothing is estimated.

**Setup.** `qwen3.6:35b-a3b` (36B total, ~3B active) at Q4_K_M on Ollama
0.31.1, RTX 3090 (24 GB), 4096-token context, temperature 0.8, top_p 0.95, one
seed per trial. Reproduce with:

```bash
python -m mironba.sim.bench --scenario configs/scenario/curry-to-lakers.yaml -n 20
```

### The numbers

Two scenarios, `curry-to-lakers` (13 completed trials, 31 calls) and
`undetermined-byc` (3 trials, 5 calls). Aggregates in `bench-curry.json` and
`bench-byc.json`; the per-trial artifacts are under `runs/`, which is
gitignored.

| | curry-to-lakers | undetermined-byc |
| --- | --- | --- |
| Schema failure, first attempt | **6.5%** (2/31) | 20.0% (1/5) |
| Unrecovered after one repair | 3.2% (1) | 0% |
| Illegal proposal, before retry | **100%** (9/9) | 100% (1/1) |
| Illegal proposal, after retry | **100%** (9/9) | 100% (1/1) |
| Trials that retried | 9 | 1 |
| Latency mean / median / p90 | 49.7s / 29.5s / 81.8s | 31.8s / 22.5s / 48.9s |

Read the schema-failure rate with its cause attached. In an earlier 41-call
batch, **all four first-attempt failures were truncations** — the model was
filling the form correctly and ran out of `max_tokens`, which is our
configuration defect and not a statement about the model. `max_tokens` went
1024 -> 2048 because of it. Two of the two failures above were likewise
truncations, so the genuine schema-*following* failure rate across everything
measured is **0**: not one well-formed completion used the wrong shape once the
model could actually see the schema.

**The retry never rescued a proposal.** Nine rejected trades, nine rejection
reasons handed back verbatim, nine still-rejected revisions. The model does
respond to the feedback — it drops players, shrinks packages, tries to fix
roster counts — but it does not solve apron salary matching. Across roughly a
dozen live proposals it never produced a legal trade. That is the single most
useful thing M1 measured, and it is an argument for the charter's boundary
rather than against the model: an LLM that could be talked into legality would
be far more dangerous than one that is simply, visibly wrong.

**`UNDETERMINED` was never reached live.** It is exercised deterministically in
`tests/test_boundary.py` end to end through the same `assemble` -> `judge`
path, but no live trial produced it, because a definite ERROR outranks
UNDETERMINED by design and every live proposal was illegal on the numbers
first. The first version of `undetermined-byc.yaml` made this impossible on
purpose without noticing: it used a second-apron team, so every proposal drew a
salary-matching error before BYC could matter. It now uses a team with cap
room. The path is still unobserved live.

**Latency spans two machine states and should not be quoted as one number.**
The 49.7s mean is much worse than the 5.96s mean / 4.58s median measured in an
earlier clean batch on the same model, same quantization, same prompts. The
difference is the GPU/CPU offload split, which changed between batches after a
leaked runner was killed and the model reloaded. **The manifest does not record
that split**, which is a real gap for M5: comparing two models on latency is
meaningless if one of them was half on the CPU and nothing says so.
`/api/ps` exposes `size_vram` against `size`; that belongs in the manifest
before any M5 comparison is run.

### Structured output: what actually happened

The charter's first defence is to constrain decoding at the server rather than
ask nicely in the prompt. **On this setup that defence does not exist.**

Ollama 0.31.1 accepted the `format` parameter carrying a full JSON schema and
ignored it. The evidence is in the raw logs: asked for a `TradeProposal`, the
model returned

```json
{"trade": {"sent_ids": ["reaveau01", "hachiru01"], "received_ids": ["curryst01"]}}
```

— a sensible shape, and not the one in the schema. A grammar cannot emit that.
The legacy `format: "json"` mode was ignored too, returning free prose. Sending
the same schema through the OpenAI-compatible endpoint as `response_format`
crashed the runner with a CUDA launch failure.

This mattered more than a missing optimisation, because the code *claimed* the
defence was working. `OllamaProvider.enforces_schema()` returned `True`, which
did two harmful things: it suppressed the prompt-level fallback, and it stamped
`schema_enforced_by_server: true` into every log line. The first measured
failure rate was therefore describing a defence that never ran, against a model
that had never been shown the schema in any form.

`enforces_schema()` now means *"we have verified this server constrains
decoding"* and returns `False` everywhere, including for vLLM, which we have
not tested. The schema is still always sent — and when enforcement is
unverified, it also goes into the prompt.

### Four bugs the measurement found

None of these were visible from the test suite, which is the point of measuring
against a live model rather than a mock.

**1. The schema was never in the prompt.** Covered above. Fixed by defence 1b:
when server enforcement is unverified, the field list goes into the prompt.

**2. Pasting the raw JSON Schema made it worse.** The obvious first fix was to
dump `model_json_schema()` into the prompt. The model promptly answered with
the schema document itself — `{"description": "Step one...", "required":
["action", "reason"]}` — because a schema document looks exactly like a JSON
object to a model being asked for a JSON object. Replaced with a compact field
list and a skeleton to imitate (`render_field_list`). Small models copy
examples far more reliably than they interpret specifications.

**3. `max_tokens: 1024` truncated completions mid-JSON**, and every truncation
was counted as a schema failure — 4 of 41 calls in the first batch. The model
was filling the form correctly and ran out of room. `RawCompletion.truncated`
existed precisely to separate these two things and the metric was not using it.
Now 2048, and truncations are reported alongside the failure rate rather than
folded into it.

**4. A prose-length cap was being counted as a schema failure.** `reason` had
`max_length=400`. A verbose but perfectly well-formed rationale therefore
failed validation and landed in the headline number as if the model could not
fill the form. The cap is now 4000 and exists only to bound storage; brevity is
`max_tokens`'s job, and structure is what the schema is for.

After the fixes, the same scenario and seed that had failed twice completed
with **0 schema failures**.

### Runtime notes, not findings

Conditions specific to this machine, recorded so the latency figures mean
something and so the next person does not rediscover them:

- The model needs ~21.5 GB of VRAM and would not load until several GB of RAM
  and VRAM were freed. With a display attached, WDDM would not serve a
  contiguous 12.9 GB CUDA buffer even with 22.3 GiB nominally free.
- At an 8192-token context, `llama-server` crashed with `std::bad_alloc` while
  saving a 102 MiB prompt-cache state on the *second* request of a session —
  which is exactly what a two-step agent does. Dropping to 4096 avoided it.
  `configs/models.yaml` pins 4096 for that reason, not for a modelling one.
- Printing a roster crashed on `cp1252` the moment a name contained `č`. NBA
  rosters are full of them. `use_utf8_console()` now reconfigures stdout, with
  errors replaced rather than raised — losing a completed run at the display
  step is absurd.

### What M1 does not show

One agent, one tick, one counterparty, two scenarios. The illegal-proposal rate
is a property of *this prompt on this model at this temperature*, not of local
models generally, and n is small enough that the difference between the
before-retry and after-retry rates should be read as a direction rather than a
measurement. `local_deep` (`qwen3.6:27b`) is declared in `configs/models.yaml`
but not pulled, so nothing here says anything about a dense model. That
comparison is M5's job, which is why the manifest records what it records.

## M1.5: what changed when the model stopped proposing

M1's number is the premise of this section, so it stays visible: **0 legal
proposals in 12 live attempts, 9 repair retries, 0 rescued.** Its manifests are
described under [M1](#m1-what-the-live-model-actually-did). Everything here is
the consequence.

### The architecture

The model states a `TradeIntent` — players it wants, players it will give up,
players it refuses to give up, and an ordering over the willing set. It cannot
express a package, a salary, or a verdict. `rules/solver.py` then enumerates
subsets of the tradeable contracts, prunes on the salary-matching bound, and
runs every survivor through `validate_trade`. Only packages with no ERROR
finding come back. The model picks one by index or declines all.

Three properties hold by construction rather than by encouragement:

- **Every option shown is legal.** The solver never re-implements a rule; it
  calls the validator and discards what the validator rejects.
  `test_solver_and_validator_never_disagree` re-validates every returned package
  over 120 generated intents.
- **No salary crosses the boundary.** Figures come from the snapshot inside
  `solver.build_trade`, which is the only place a `Trade` is constructed.
- **UNDETERMINED is legal-so-far, not illegal.** Base-year compensation is an
  unknown; every snapshot-derived player is UNKNOWN for re-sign status, so
  discarding those would return nothing on real data.

### Solver latency

Measured on this machine, 40 randomised intents per row:

| Tradeable assets | Max out | p50 | worst |
| --- | --- | --- | --- |
| 8 | 2 | 0.6 ms | 1.4 ms |
| 14 (realistic roster) | 4 | **10.6 ms** | **27.7 ms** |
| 20 | 4 | 44 ms | 118 ms |
| 25 | 4 | 106 ms | 345 ms |

Growth is roughly C(n,4). A 15-man roster is the realistic ceiling, so M3
fanning out to 20 agents a tick costs well under a second of solver time in
total. Reported now rather than discovered at M3.

### Schema enforcement: measured, not assumed

M1 logged `schema_enforced_by_server: true` because the code *sent* a schema.
`llm/probe.py` now measures it: it asks a question whose natural answer is
prose, supplies the schema **only** through the server parameter, never in the
prompt, and checks whether the reply conforms. A probe the model would pass
anyway proves nothing, which is the trap the M1 flag fell into.

> **Superseded at M1.6, and kept.** Everything in this subsection is a correct
> measurement of Ollama **0.31.1**. On **0.32.5**, the same probe against the
> same two models returns **9 of 9 conformed** on every shape. The capability
> was added between versions; the measurement was not wrong. See
> [Schema enforcement, one version later](#schema-enforcement-one-version-later).

Result on Ollama 0.31.1 / qwen3.6:35b-a3b — **0 of 9 conformed**:

| Schema shape | Conformed | Verdict |
| --- | --- | --- |
| Flat, no `$refs` | 0/3 | not enforced |
| With `$defs`/`$ref` | 0/3 | not enforced |
| Same, refs inlined | 0/3 | not enforced |

**`$defs` is not the cause.** The flat schema fails identically, so the
reference-following hypothesis is dead: this server accepts `format` and
ignores it entirely. `inline_refs()` exists anyway, because the hypothesis was
worth testing and the next server may differ.

The log flag is now `schema_enforcement_observed`, derived from that probe and
cached once per process. `None` means it could not be measured — distinct from
`False`, because "unreachable" and "ignored the schema" are different facts.

The same server also ignores `num_gpu`, which is why the measurements below run
on CPU.

### A rules bug the solver found

`_self_consistent_tier` resolves the circularity between "how much can this team
take back" and "which tier does that leave it in". It iterated `UNDER_CAP,
FIRST_APRON, SECOND_APRON` and **skipped `OVER_CAP`**. A team over the cap but
below the first apron — most of the league, most of the time — therefore fell
through to the apron branch and received flat 100% matching instead of the
bracket table. At $20M outgoing that is $20M back rather than $27,752,000.

The M0 coverage matrix was 16/16 FORMULA green throughout, and stayed green
after the fix: every FORMULA fixture either sat under the cap or passed
`post_trade_tier` explicitly, so the gap was in the *resolution* of the tier
rather than in any bracket. It surfaced only because the solver could not
construct a legal package for an ordinary over-cap team and the arithmetic had
to be reconciled by hand.

This means M1's 0/12 was measured against an over-strict validator. The
architecture change is still justified — the model was failing on apron teams,
where the rule was correct — but the old number was worse than the rule alone
warranted, and that is worth stating plainly rather than letting it stand as
pure evidence for the redesign.

Pinned by `TestSelfConsistentTierCoversEveryTier`.

### Run artifacts are append-only

`runs/` is never deleted from inside the package, and a test greps for
`rmtree`, `unlink`, `os.remove` and `os.rmdir` across `mironba/` to keep it that
way. Written after an M1 benchmark's artifacts were deleted to tidy up before a
replacement run that then failed, destroying the only complete measurement then
in existence. A codebase whose rule is "no manifest, no result" should not ship
a convenient way to destroy manifests. Cleanup is a manual act, outside the
code.

### The numbers

> **Kept, and marked.** These runs are CPU-bound (`gpu_fraction: 0.0`) and were
> made against a validator whose search prune was too strict — see
> [A prune that deleted legal packages](#a-prune-that-deleted-legal-packages).
> The satisfiability figures survive that fix: the two intents in question
> wanted Curry at $55.8M and Markkanen at $42.2M, and the prune was wrong by at
> most $1.6M. The latency figures do not survive the move to a GPU-resident
> model and should not be compared with anything below.

16 trials across two scenarios, 30 LLM calls, on CPU (`gpu_fraction: 0.0`
recorded in every manifest — the latency below is CPU-bound and not comparable
to a GPU-resident run).

| | curry-to-lakers (LAL, 2nd apron) | undetermined-byc (DET, cap room) |
| --- | --- | --- |
| Trials / LLM calls | 8 / 20 | 8 / 10 |
| Schema failure, first attempt | **0%** (0/20) | **0%** (0/10) |
| Intents stated | 6 | 1 |
| **Intent satisfiable (first)** | **0%** | **0%** |
| **Intent satisfiable (final)** | **0%** | **0%** |
| Revisions that rescued | 0/6 | 0/1 |
| Packages per satisfiable intent | n/a — none were | n/a |
| Declined all legal options | 0 | 0 |
| Solver p50 / worst | <1 ms / <1 ms | <1 ms / <1 ms |
| Binding constraint | SALARY_MATCH ×12 | SALARY_MATCH ×2 |
| Latency mean / median / p90 | 41.4s / 36.8s / 60.3s | 27.4s / 22.4s / 45.7s |
| Outcomes | 6 unsatisfiable, 2 stood pat | 1 unsatisfiable, 7 stood pat |

Legal-proposal rate is omitted deliberately. It is 100% by construction and
reporting it would be self-congratulation.

**The bottleneck moved rather than disappeared.** The model no longer emits
illegal packages, because it cannot. What it does instead is want things it
cannot afford. Every one of the seven intents was blocked on SALARY_MATCH, and
the solver was right every time — checked by hand on the clearest case: Detroit
is "under the cap" by **$207,451**, and the model offered Dennis Schröder
($13,025,250) for Lauri Markkanen ($42,176,400), which is $21.4M short of any
legal structure.

**The revised intent never converted an unsatisfiable want into a satisfiable
one — 0 of 7.** It is not inert, though, and the difference from M1's retry is
visible in the transcripts: handed "you were $7,824,647 short of $55,761,216",
the model moved from Curry ($55.7M) to Jimmy Butler ($48.8M) and reasoned
explicitly about the gap. It closed most of the distance and still missed, by
$862,108. Directionally responsive, quantitatively short.

**The selection step was never exercised live.** No intent was satisfiable, so
the model never saw an options list and never had the chance to decline one.
Steps 1 and 2 are measured; step 3 is covered only by the offline suite.

### Is UNDETERMINED reachable? Yes — but not observed from a model intent

Requirement checked directly, with a hand-supplied feasible intent on the
cap-room scenario rather than a model-generated one:

```
target Josh Richardson ($3,051,153) -> 3 legal package(s)
  chosen:  send Marcus Sasser -> receive Josh Richardson
  VERDICT: UNDETERMINED
  BASE_YEAR_COMPENSATION: ... we do not know whether he re-signed ...
```

So the masking is genuinely gone: a package that is legal on every decidable
axis now surfaces BYC as the only open question, which is what M0 built a third
verdict for. It has still never been reached from an LLM-generated intent,
because no LLM-generated intent has been satisfiable.

### What this says to do next

The diagnosis is specific and the fix is not a prompt tweak. The intent prompt
shows the model a roster and a payroll and asks what it wants, while withholding
the one number that determines what is possible: how much salary it can take
back. The solver computes that for free. Telling the model "given the assets you
are willing to move, the most expensive player you can absorb is $X" is a
deterministic fact from the same layer that already owns the arithmetic — the
M1.5 principle applied one step earlier, not a prompt being tuned against a
disappointing result.

That is deliberately **not** implemented here. It would change the measurement
above, and the measurement above is what justifies it.

## M1.6: what changed when the model was told what was possible

The lever named at the end of M1.5, pulled. The shape of the fix is the M1.5
principle applied one step earlier — the deterministic layer that owns the
arithmetic computes the answer, and the model chooses within it — with one
constraint carried over intact: **the model is told *who*, never *how much*.**

### The model switch, confirmed first

Every M1 and M1.5 latency figure was measured on a model Ollama had placed
entirely on the CPU. `qwen3.6:35b-a3b` is 22.3 GiB of weights against 24 GB of
VRAM, and `num_gpu` did not move it. `qwen3.6:27b` is 16.2 GiB and loads whole:

```
  residency  100.0% on GPU of 15.9 GiB
```

`gpu_fraction: 1.0` in every manifest below, so the numbers mean something on
this hardware. Measured throughput: **35 tok/s** generation, ~3100 tok/s
prefill. Confirmed before benching, because on a partial offload none of the
rest would have been worth collecting.

Two costs came with the switch, both measured rather than assumed:

- **`thinking: true` dominates wall-clock.** The same prompt takes **7.4s**
  with thinking off and **122s** with it on — the model spends its whole
  budget reasoning (14k characters, 4096 tokens) before answering. Ollama
  accepts `think: "low"|"medium"|"high"` but qwen3.6 produces byte-identical
  output for all three, so the reasoning length cannot be bounded. `max_tokens`
  went to 6144 because 4096 truncated one call in four, and a truncation
  counted as a schema failure is the exact contamination `max_tokens` caused at
  M1.
- **A schema that would not compile.** See below.

### A `maxLength` the grammar compiler would not take

The first call on the new server failed outright:

```
HTTP 400 "Failed to initialize samplers: failed to parse grammar"
```

`REASON_MAX` was 4000. Bisected against the live server:

| `maxLength` | Compiles |
| --- | --- |
| ≤ 1999 | yes |
| ≥ 2000 | no |

Nothing else tripped it — enums, `$defs`/`$ref`, integer minimums and array
bounds all compile. The compiler appears to expand a string bound into a
bounded repetition and give up past a ceiling.

Now 1500, and the reasoning behind the old value has **inverted** rather than
merely loosened. 4000 was chosen so a verbose rationale could not be scored as a
schema failure — correct while the schema was being ignored and `maxLength` was
a *validation* limit. Under an enforced grammar it is a *decoding* limit: the
model is made to close the string, so a long reason is clipped instead of
failing the parse. A hermetic test now walks every agent schema for bounds over
1600. The failure was loud, which is the good case, but it was loud at call time
and no test before this one could have seen it.

### Schema enforcement, one version later

Same probe, same two models, upgraded server:

| Server | Model | Flat | With `$defs` | Inlined |
| --- | --- | --- | --- | --- |
| 0.31.1 | qwen3.6:35b-a3b | 0/3 | 0/3 | 0/3 |
| 0.32.5 | qwen3.6:35b-a3b | **3/3** | **3/3** | **3/3** |
| 0.32.5 | qwen3.6:27b | **3/3** | **3/3** | **3/3** |

The capability was added between versions. Note the second row: it is the *same
model* that failed 0/9 at M1.5, so this is not a property of the model, and
attributing it to one would have been the easy mistake. An unparseable schema is
now a hard HTTP 400 instead of a silent no-op, which is the honest behaviour and
is what surfaced the `maxLength` cliff above.

`OllamaProvider.enforces_schema()` still returns `False`. The capability is real
but version-dependent, and that method cannot see a version; returning `True`
would restore precisely the M1 defect of a static claim standing in for a fact
about the running process. The client continues to take its flag from
`observed_enforcement()`, which measures it per `(server, base_url, model)`.

### A prune that deleted legal packages

`solve` skips subsets that fail a cheap arithmetic bound before building a
`Trade`. That prune called `max_incoming_salary` with no `post_trade_tier`,
which answers a deliberately conservative question: `_self_consistent_tier` must
pick one tier, and a tier the team would be pushed out of is not
self-consistent.

Golden State at $176,540,943 is $1.6M under the first apron. Sending $8M out:

| | |
| --- | --- |
| Bracket table allows | $15,752,000 |
| …but that lands them at $184.3M, over the apron | |
| So the self-consistent tier collapses to flat 100% | $8,000,000 |
| True ceiling: enough to land $1 below the apron | **$9,591,056** |

Harmless inside `validate_trade`, which knows the actual incoming salary and
passes the resulting tier explicitly. Not harmless as a prune — nothing runs
behind a prune to catch what it dropped. This one dropped **twelve legal Lakers
packages, every single one**, so the first run of the M1.6 pre-filter reported
that the Lakers could acquire nobody at all. That reads as a finding about an
apron team with no flexibility. It was an artifact of the search.

`matching_upper_bound` takes the maximum over every tier, so whatever tier the
trade lands in, the bound is at least that tier's limit. A prune may over-admit
and pay for it in wasted validations; it may never under-admit.

Tested two ways, because one of them would have missed it. A grid asserts the
bound dominates every tier's exact limit. A brute-force fixture on the real
Lakers and Warriors payrolls enumerates every subset with no bound at all and
asserts the pruned solver finds all of them — the synthetic fixtures never sat
near an apron, which is the only place the two answers differ.

This does **not** overturn M1.5's satisfiability result. Those intents wanted
Curry at $55.8M and Markkanen at $42.2M; a $1.6M correction does not reach them.

### The pre-filter

Two passes, in cost order:

1. **Bound.** One arithmetic ceiling for the whole roster — the loosest limit
   any subset could justify — then one comparison per partner contract. Drops
   the unaffordable majority without constructing a single `Trade`. O(1) per
   player.
2. **Solve.** A full `solve` per survivor, one target at a time. Every name that
   comes back is backed by a validated package, not by a bound that merely
   failed to rule it out.

Both are timed separately, because they scale differently and a blended number
would hide which one matters at M3.

What reaches the model is a list of people and two counts:

```
  sextoco01    Collin Sexton              5 ways, from 1 player out
  clarkjo01    Jordan Clarkson           10 ways, from 1 player out
```

No salary, no cap figure, no dollar amount. `FeasibleTarget` is checked field by
field and again on its rendered text, because a clean field name does not stop a
renderer leaking a figure. Counts are safe and useful — "three ways to get him"
is a fact about flexibility, not a quantity that can be used to argue a trade
into legality.

Feasibility is computed **one target at a time and is not additive**: two
individually-acquirable players may still be unaffordable together, which is why
the revise-intent path stays.

### The third scenario

LAL and DET are the two extremes, and between them they miss the league. Above
the first apron, `max_incoming_salary` takes the flat percentage. Under the cap,
it takes the cap-room branch. Neither had ever consulted `exception_match_limit`
— the three-formula median most of the league actually trades under, and the
function whose tier dispatch was wrong until M1.5 with every test still green.

**Chicago**, on figures from the snapshot:

| | |
| --- | --- |
| Payroll | $165,919,354 |
| Salary cap | $140,588,000 → over it |
| First apron | $178,132,000 → $12.2M below it |
| Tier | `OVER_CAP` |
| Mid-sized contracts | Ball $21.4M, Vučević $20.0M, P. Williams $18.0M, Huerter $16.8M, Collins $16.7M |

At $20M outgoing the bracket table allows **$27,752,000** back where apron
matching would allow $20,000,000, so the uncovered path is demonstrably live.

**"Expiring" is deliberately not claimed.** The ingest carries a season and a
salary, not a contract end year, so no scenario can source expiry and this one
does not pretend to. What is sourced is the tier and the contract sizes.
Asserting expiry from memory would be the same class of error as assuming
base-year compensation, and that one is at least flagged.

Against Utah, 13 of 14 contracts are acquirable and exactly one is not:
**Markkanen at $42,176,400** — the player the Detroit GM chased through both its
intents in M1.5. So the scenario can distinguish "picks someone reachable" from
"picks the best player on the board regardless".

### The bottleneck moved again: acquirable ≠ acquirable *on these terms*

The list fixed target selection outright — the Lakers GM went from naming an
unreachable target in **12 of 12** blind intents to **0 of 12** with the list.
First-attempt satisfiability did not follow it up, and the reason is specific
enough to be worth stating exactly.

The scan answers an unconditional question: *given everything on your roster,
who could you get?* The model then answers a different one, because a
`TradeIntent` also names what it is **willing to part with**. Those two do not
have to agree, and repeatedly they did not:

```
scan says      paytoga02  Gary Payton     1 way, from 1 player out
model wants    paytoga02
model offers   morrima02, reddica01, hayesja02, miltosh01   (four minimums)
model excludes ..., vandeja01, ...
the one legal package:  send Jarred Vanderbilt -> receive Gary Payton
```

The model asked for a player the list promised, then explicitly excluded the
only asset that delivers him. `1 way, from 1 player out` says how many bodies it
takes and never says **which**, so an intent can satisfy the list and still be
unsatisfiable — not because feasibility was wrong, but because it was
conditional on a pool the model then narrowed.

This is the same shape as the M1.5 result one level down, and the next lever is
nameable for the same reason: the solver already knows which contracts unlock
each target and could say so, and naming a player is not naming a price. It is
**deliberately not implemented here**, on the same grounds as last time — it
would change the measurement that justifies it.

Non-additivity is the smaller second cause. Feasibility is computed one target
at a time, so an intent naming two individually-acquirable players can still be
unaffordable, which is why the revise path stays and why it now converts.

### The numbers

**72 trials, 201 LLM calls, three scenarios, both arms, `gpu_fraction: 1.0` in
every manifest.** `qwen3.6:27b`, Q4_K_M, thinking on, 16384 context, one seed
per trial recorded in that trial's manifest.

| | LAL — 1st apron | | DET — cap room | | CHI — over cap | |
| --- | --- | --- | --- | --- | --- | --- |
| | blind | feasible | blind | feasible | blind | feasible |
| Trials / LLM calls | 12 / 36 | 12 / 40 | 12 / 23 | 12 / 22 | 12 / 43 | 12 / 37 |
| Intents stated | 12 | 12 | 5 | 5 | 12 | 12 |
| **Named an unreachable target** | **12/12** | **0/12** | 0/5 | 0/5 | **7/12** | **0/12** |
| **Satisfiable, first attempt** | **0%** | **8.3%** | 80% | 100% | **41.7%** | **91.7%** |
| **Satisfiable, final** | **0%** | **41.7%** | 100% | 100% | 100% | 100% |
| Revisions that rescued | 0/12 | 4/11 | 1/1 | 0/0 | 7/7 | 1/1 |
| Packages per satisfiable intent | — | 1.6 | 1.0 | 1.0 | 1.17 | 1.58 |
| Selected a package | 0 | 2 | 1 | 0 | 12 | 11 |
| Declined all legal options | 0 | 3 | 4 | 5 | 0 | 1 |
| Feasible targets available | 4 | 4 | 11 | 11 | 13 | 13 |
| Schema failure, first attempt | 0% | 0% | 0% | 0% | 2.3% | 2.7% |
| Latency mean / p90 | 90.2s / 110.6s | 91.6s / 121.3s | 69.9s / 92.4s | 71.1s / 86.4s | 80.1s / 103.0s | 88.7s / 122.5s |

Aggregated over all 29 intents in each arm:

| | blind | feasible | delta |
| --- | --- | --- | --- |
| **Named an unreachable target** | 65.5% (19/29) | **0.0%** (0/29) | **−65.5pt** |
| **Intent satisfiable, first attempt** | 31.0% (9/29) | **58.6%** (17/29) | **+27.6pt** |
| **Intent satisfiable, final** | 58.6% (17/29) | **75.9%** (22/29) | **+17.2pt** |
| LLM calls spent | 102 | 99 | −3 |

**Supplying solver-computed feasibility moved first-attempt satisfiability from
31.0% to 58.6%, and eliminated unreachable targets entirely — 65.5% to 0%.**
The second number is the cleaner result: it is the thing the intervention
directly controls, and it went to zero in all three scenarios.

It also costs nothing. The feasible arm spent *fewer* LLM calls (99 vs 102)
because it needed fewer revisions, so the list pays for itself in the same
budget it improves.

**The blind arm is not M1.5's blind arm, and that is the point of keeping it.**
Detroit blind measures 80% first-attempt here against M1.5's 0%. Almost none of
that is the list — it is the model change and the prune fix. Had M1.5's number
been reused as the baseline, the list would have been credited with a swing it
did not produce. Re-measuring both arms on the same model, same code and same
seeds is what makes the delta attributable.

**Team flexibility dominates the absolute level.** Chicago reaches 100% final
satisfiability in *both* arms and trades in 12 of 12 blind trials; Los Angeles
reaches 41.7% at best. An apron team has few legal moves and the list cannot
invent any. What the list changes for Chicago is efficiency — 41.7% → 91.7%
first-attempt, so the answer arrives without a repair round.

### Step 3, exercised for the first time

Never reached live at M1.5. Here it ran **39 times**: 26 selections and 13
declines.

| Index chosen | Times |
| --- | --- |
| 0 | 21 |
| 1 | 5 |
| declined all | 13 |

**Not defaulting to zero.** Index 1 is chosen 5 of 26 times, and the selection
prose discriminates explicitly:

> "Package [0] trades Markieff Morris for Trayce Jackson-Davis. […] The other
> packages involve trading established contributors like Hayes or Reddish for a
> similar…"

**The declines are coherent and persona-consistent.** Every one cites the
structured persona parameters by name and makes a basketball argument rather
than a legality argument — which is correct, since legality is no longer the
model's problem:

> "The proposed trade (Vanderbilt for Gary Payton II) does not provide a
> meaningful on-court upgrade for a championship-contending roster already
> featuring LeBron James and Luka Dončić. With a win_now_horizon of 1…"

> "With a high asset-hoarding preference (0.8) and low risk tolerance (0.3), I
> prefer to maintain roster stability rather than execute a lateral or
> downgrade swap."

The decline rate tracks the persona in the direction it should: Detroit's
`cautious-hoarder` (asset_hoarding 0.8, one asset out) stood pat in 7 of 12
trials and declined 9 of the 10 package sets it was shown, while Chicago's
`balanced` GM declined once in 23. That is the persona parameters feeding
behaviour, not prose flavour.

### UNDETERMINED, from a model-generated intent

Reached at M1.6 without any hand-supplied step — the M1.5 requirement that was
still outstanding:

```
run  undetermined-byc-20260731T092127Z-54c53999   arm=blind  gpu_fraction=1.0
  INTENT   targets=['hendrta01'] tradeable=['beaslma01']     <- model
  SOLVER   satisfiable=True packages=1
  SELECTED idx=0 declined=False                              <- model
  VERDICT  UNDETERMINED
     BASE_YEAR_COMPENSATION: [UTA] Taylor Hendricks may be a base-year-
     compensation player: we do not know whether he re-signed with this team
```

Once in 72 trials, which is the honest frequency: it needs a satisfiable intent,
a selected package, and the `unresolved` BYC scenario, and only one of the three
scenarios leaves BYC unresolved.

### Latency, split as required

| | pre-filter | target scan | per-intent solve |
| --- | --- | --- | --- |
| p50 | **0.03 ms** | 3.3–227 ms | 0.1–0.4 ms |
| worst | **0.05 ms** | 263 ms | 10.9 ms |

The pre-filter is ~100–7500× cheaper than the scan behind it, which is the
whole reason for doing it first. The scan's spread is the persona: Los Angeles
allows 4 assets out and pays 227 ms; Detroit allows 1 and pays 3.3 ms. Growth
is C(n, max_assets_out) per surviving target.

Against ~80 s of model latency per call, all deterministic work is under 0.3%
of a tick. Nothing here constrains M3.

### What M1.6 does not show

- **One model, one quantization.** Every figure is `qwen3.6:27b` Q4_K_M. M5 is
  where this becomes a comparison.
- **12 trials per cell.** A 27.6pt aggregate delta is well clear of the noise;
  the per-scenario first-attempt figures are not, individually.
- **Thinking is on and costs ~16×** (7.4 s vs 122 s on an identical prompt).
  Whether it changes decision *quality* is unmeasured — it was held constant,
  not varied.
- **Two truncations remain** at `max_tokens: 6144`, both on Chicago, and both
  are the entire schema-failure rate (1 call in ~100 per arm). They are a
  budget artifact, not a form-filling failure.
- **Step 3 still chooses from short lists** — 1.0 to 1.6 packages per
  satisfiable intent. "Which of six" has not been tested live.
