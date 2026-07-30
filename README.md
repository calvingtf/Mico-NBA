# MiroNBA

Multi-agent simulation of counterfactual NBA scenarios. See `CLAUDE.md` for the
project charter; this file records what actually exists.

Part of the MiroFish/MiroShark family. Package, charter, and repo folder all
read `MiroNBA`/`mironba`.

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
tests/         245 tests, all passing
```

Nothing in `models/`, `world/`, `llm/`, `agents/`, `sim/`, `eval/`, or `api/`
yet. That is deliberate. **M1 is open.**

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
