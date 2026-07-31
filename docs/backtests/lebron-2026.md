# Backtest target: LeBron James, 2026 free agency

**Status: recorded, not implemented.** There is no scenario file and no
simulation. This document, its two evidence files, `mironba/world/evidence.py`
and `mironba/rules/signing.py` are the whole of it. The missing piece is a
pending-decision primitive — a world state that can hold "LeBron has not
decided" as a first-class fact that other agents block on — and inventing one
here would be speculative plumbing around the one thing that must not leak.

## Why this event

M4 needs a counterfactual with a known outcome, a visible causal structure, and
a clean information boundary. This one has all three:

- **A single decision forks the league.** One player's choice determines what
  four other teams do next.
- **Teams were visibly blocked.** Golden State held roster space open and
  finished July as the only team in the NBA that had not acquired a new player.
  That is a *stated, sourced* waiting state, not an inference.
- **The outcome is unambiguous and already in our ingest.** James is on
  Philadelphia's books. No judgement call about what "happened".

## The freeze

```
FREEZE = 2026-07-06T16:01:00Z   (12:01 p.m. ET, July 6 2026)
```

Chosen as the instant the July moratorium ends and free-agent contracts may
first be signed. Three reasons, in order of weight:

1. **Before it, world state is not well defined.** Deals agreed during the
   moratorium (July 1–6) are not binding and either side may withdraw. A
   simulator seeded mid-moratorium would be reasoning over agreements that
   might evaporate.
2. **It is a league-calendar boundary, not a convenience.** It does not depend
   on when anything was retrieved or on what our snapshot happens to contain.
3. **It leaves the question open.** James decided on July 24 — eighteen days
   later. The blocked state is live at the freeze, and the outcome is not.

Green's opt-out (June 29) sits *before* the freeze, so it is an input. That is
the point: the opt-out is the setup, not the result.

## The cutoff rule

> **No fact dated after the freeze may enter world state.**

Enforced in three places, because a rule applied at one call site is a rule
that one call site can forget.

**1. The partition is structural.** `EvidenceLedger` exposes `world_state()`,
which returns PRE items only. POST items are reachable exclusively through
`ground_truth(unlock=SCORING_UNLOCK)`. The token is a plain string and buys
nothing against a determined caller — that is deliberate. What it buys is that
reading the answer shows up in a diff and can be grepped for.

**2. The label is checked, not trusted.** `EvidenceLedger.validate()` recomputes
each item's phase from its date and refuses to load a file where the two
disagree. A row dated July 24 but marked PRE is the single error this whole
mechanism exists to prevent, so it is a load-bearing assertion rather than a
lint. `test_a_post_freeze_date_labelled_pre_fails_validation` covers it.

**3. Nothing outside `eval/` may read the answer.**
`test_only_eval_may_name_the_unlock` greps every module in the package for
`SCORING_UNLOCK` or `ground_truth` and fails if anything outside `eval/`
mentions either. A companion test asserts `sim/` and `agents/` do not import
`world.evidence` at all.

### The ingest leaks too — and this is the part that would have bitten

The evidence file is not the only door. **Our own transaction log runs to
2026-07-09**, three days past the freeze:

```
2026-07-09   The Golden State Warriors signed Charles Bassey.
```

A world state assembled straight from `bbref-2025-26` would hand the simulator
a post-freeze signing that arrives looking like ordinary roster data. Worse, it
is a Golden State signing — precisely the team whose behaviour the backtest is
trying to predict.

`evidence.redact_after(rows, freeze, key="date")` drops them, and
`TestTheIngestLeaksToo` asserts both that the hazard is real in the current
snapshot and that the filter removes it. It lives in `world/evidence.py` rather
than in `data/` because the freeze is a property of the backtest, not of the
snapshot: the same snapshot is legitimate input for a scenario frozen later.

The same applies to the **contracts** snapshot, `bbref-contracts-2026-27`,
which was retrieved on 2026-07-31 and therefore already contains James on
Philadelphia. It is ground truth for this backtest and must not be loaded as
world state at all. There is no date column to filter on — it is a snapshot of
*now* — so the protection has to be that M4 does not load it.

## The blocked state at the freeze

| Team | Status at freeze | Evidence |
| --- | --- | --- |
| **GSW** | Holding roster space open for James; signs nobody new | LBJ-04, COND-03 |
| **CLE** | Tied to James | LBJ-04 |
| **MIA** | Tied to James | LBJ-04 |
| **MIN** | Tied to James | LBJ-04 |
| **PHI** | Tied to James | LBJ-04 |

Golden State is the strongest case and the one worth scoring: the waiting is
explicit in the reporting, has a visible consequence (no signings for a month),
and resolves into a documented set of moves the day after the decision.

## What actually happened (POST — ground truth, not input)

| Date | Event |
| --- | --- |
| 2026-07-14 | Charania reports the focus narrowing to CLE, MIA, PHI; all five still in the mix |
| 2026-07-23 | Golden State is the only NBA team yet to acquire a new player |
| **2026-07-24** | **James announces Philadelphia** — 2 years, ~$8M, player option |
| 2026-07-28 | Green re-signs with Golden State, 1 year, $27,678,571 |
| 2026-07-28 | Golden State retains Horford, Porziņģis, Bassey; Melton signed pending an exception |
| — | Quinten Post is under contract with Memphis for three seasons |

James's terms from our own ingest, which is what makes "~$8M" concrete:
**$3,876,529 in 2026-27 plus a $4,070,355 player option for 2027-28** =
$7,946,884.

**Cleveland, Miami and Minnesota have no sourced post-decision moves in this
file.** I did not find reporting on what they did next, and I am not going to
supply it from memory. Scoring at M4 should either restrict itself to Golden
State and Philadelphia or the evidence file should be extended first.

## Conditional commitments

The type that makes the branch fork, in `world/evidence.py`:

```python
ConditionalCommitment(subject, condition, commitment, reported_by, date, ...)
```

"Golden State will keep a roster spot open" is not a fact about Golden State's
roster. It is a fact about what that roster *becomes under each branch* of a
decision nobody had made yet. Flattened into an ordinary dated fact it loses
the antecedent — and the antecedent is the causal structure a counterfactual
simulator exists to reproduce.

Four are PRE-freeze and therefore live at simulation time:

| id | subject | condition | commitment |
| --- | --- | --- | --- |
| COND-01 | GSW | IF James signs with GSW | Green's declined option is the room to absorb him |
| COND-02 | GSW | IF James signs with GSW | GSW also pursues Anthony Davis |
| COND-03 | GSW | UNTIL James declares | GSW holds roster space and signs nobody |
| COND-04 | greendr01 | IF Green re-signs | Green returns rather than testing the market |

Two are POST and are part of the answer (COND-05, COND-06).

They carry **no probability**. Nobody published one, and a fabricated number
here would end up inside the scoring path.

### The worked example, and what it exposed

Green declined a $27,678,571 player option on 2026-06-29, reported as creating
flexibility to pursue James. The conditional (COND-01) is the annotation; the
opt-out is meant to be the anchor.

**The anchor is not in our ingest.** The premise that "that opt-out is a dated
transaction your ingest should already hold" does not hold:

- zero rows in `bbref-2025-26` transactions reference `greendr01`;
- exactly one row in the whole 1,131-row file contains the word "option", and
  it is a coincidental match inside a trade description.

Basketball-Reference's transaction log records signings, trades and waivers —
**not option declines**. The July 9 Bassey *signing* is there; the June 29
opt-out is not. So `anchors` on COND-01 points at evidence-file rows (GSW-01,
GSW-02), and an anchor into the transaction table is something M4 will need a
different source for.

## Verification notes

The brief warned that one secondhand figure did not check out. Everything was
re-checked against a primary source where one exists; here is the result, so
the next reader does not have to redo it.

**Confirmed against Basketball-Reference contract pages retrieved 2026-07-31:**

| Claim | Check |
| --- | --- |
| Green's option / re-signing at $27.68M | **$27,678,571** exactly, on GSW's books |
| LeBron to Philadelphia | On PHI's books, $3,876,529 + $4,070,355 PO |
| GSW retained Bassey, Horford, Melton, Porziņģis | All four on GSW's books |
| Post left for Memphis | On MEM's books, 3 years through 2028-29 |
| Green tradeable from 2026-12-15 | Derived below, not taken on trust |

**Corrections and caveats:**

- **The opt-out is not a transaction in our ingest.** See above. This was the
  premise that did not survive checking.
- **The suitor list is longer than three.** Reporting names five teams — GSW,
  CLE, MIA, MIN *and* PHI. The brief named GSW, MIA and MIN; that is a subset,
  not an error, but Cleveland belongs in it and Philadelphia was in the field
  the whole time rather than arriving late.
- **Green's no-trade clause rests on a single source** (NBCS Bay Area, GSW-11)
  and appears in no structured feed. `contracts.csv` has a `no_trade_clause`
  column, so it is encodable — but it should stay flagged as reported-only
  until a second source is found. A no-trade clause is not a small detail for a
  trade simulator.
- **Gary Payton II is "expected" back, not signed.** He is absent from
  Golden State's contract page as of 2026-07-31, which is consistent with the
  reporting rather than contradicting it. Recorded as POST, uncorroborated.
- **GSW-08 is dated approximately.** The article says "as July nears its end";
  2026-07-23 is my reading, and it is POST under any reading.

### The December 15 date, derived rather than asserted

Green cannot be traded until **2026-12-15**, and that is now computed:

```
signed 2026-07-28  ->  three months = 2026-10-28
cap year 2026      ->  fixed date   = 2026-12-15
later of the two   ->  2026-12-15
```

The extended branch does not apply. It would need a Bird/Early Bird re-signing
by an over-cap team at **more than 120%** of prior salary; Green's prior-season
salary is $25,892,857 in our own snapshot, so $27,678,571 is **1.069×** — under
the threshold. Had it been a 25% raise the date would move to 2027-01-15, a
month past the point where a December trade market opens.

Encoded in `mironba/rules/signing.py`, sourced to
[NBA.com](https://api-hub.nba.com/news/nba-trade-deadline-explained) and
[Hoops Rumors](https://www.hoopsrumors.com/2025/09/free-agents-who-sign-after-monday-wont-be-trade-eligible-on-december-15.html),
both retrieved 2026-07-31. Deliberately *not* modelled: the separate
restrictions on traded players, sign-and-trades, and the two-month
re-acquisition bar on a waived player. Each is a different rule with a
different clock.

## Files

| Path | Contents |
| --- | --- |
| `lebron-2026-evidence.csv` | 36 dated items, 16 PRE / 20 POST |
| `lebron-2026-conditionals.csv` | 6 conditional commitments, 4 PRE / 2 POST |
| `mironba/world/evidence.py` | types, ledger, freeze enforcement, `redact_after` |
| `mironba/rules/signing.py` | post-signing trade restrictions |
| `tests/test_evidence.py` | the partition holds; mislabels are caught |
| `tests/test_signing.py` | the rule, with Green as the worked case |

Every row carries a source URL and a retrieval date. All web retrievals are
2026-07-31; the two Basketball-Reference snapshot retrievals are 2026-07-30
(transactions) and 2026-07-31 (contracts).

## What M4 still needs

1. **A pending-decision primitive.** World state must be able to hold "James
   has not decided" as a fact other agents can block on, and a tick must be
   able to resolve it. Nothing in `world/` models an unresolved decision today.
2. **A free-agency action space.** Every agent action in the codebase is a
   trade. Signing, retaining and holding a roster spot are not expressible.
3. **Scoring.** What counts as a hit — destination only, or destination plus
   the follow-on moves? Partial credit for "Golden State waited and then
   re-signed Green" is a real modelling choice and should be made before the
   sim runs, not after the numbers come in.
4. **More teams, or a narrower claim.** Three of the five blocked teams have no
   sourced follow-up here.
