"""Retrieve-then-rank: the enumerator stays wide, the ranker orders its head.

The planner's precision problem is structural. It enumerates every legal
package, so it cannot have precision — it is a *retrieval* stage, and 673
proposals against 13 real trades is what retrieval looks like when it is doing
its job. Trimming it to raise precision would trade away recall, which is the
one thing it currently does well.

So precision moves to a second stage. Recall is measured on the candidate set
(does the real trade appear at all), precision at the head (how far up).
``precision@k`` is a **different claim** from enumeration precision, not a
better version of it, and both belong in the README with their nulls.

## What this module does and does not do

It builds features and an evaluation harness. It does not report a metric on
partial data and does not tune anything: at the time of writing three of ten
seasons had finished, all from one CBA era, and fitting on that would be the
eighth retraction rather than a result.

## Significance is a permutation test, not a binomial

Proposals are not independent draws. The enumerator proposes systematically —
if it likes a team pair it proposes several packages for it — so hits are
correlated and a binomial overstates significance badly. The null here shuffles
which pairs are real, re-scores, and repeats, which preserves that correlation
structure. Same machinery as the counterparty-matching null.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

#: Features available from the ingest as it stands. Named explicitly so the
#: ones that are NOT available are visible rather than quietly absent.
AVAILABLE = (
    "salary_similarity",      # |out - in| / max(out, in), from contracts.csv
    "salary_magnitude",       # log of the larger side; big deals are rarer
    "roster_slot_distance",   # |roster_a - roster_b|, from contracts.csv
    "disposition_pair",       # buyer/seller/ambiguous each side, from standings
    "apron_tier_pair",        # tier each side, from payroll + constants
    "record_gap",             # |win_pct_a - win_pct_b| at the freeze
    "value_moving",           # prior-season box_pm36 of players in the package
    "value_gap",              # value difference between the sides
    "guaranteed_share",       # guaranteed / salary, from contracts.csv
)

#: Fields the contracts table carries but that are populated only for the
#: hand-curated evidence rows, not league-wide. Using them as features would
#: train on a handful of annotated cases and generalise to nothing.
NOT_AVAILABLE = (
    "contract_end_year",          # not in the ingest at all
    "player_option / team_option",  # contract_type is present but sparse
    "no_trade_clause",            # column exists, populated for ~0 rows
    "trade_restricted_until",     # same
    "draft_pick_value",           # picks are unvalued; the M8 stated limit
    "injury / availability",      # never ingested; a third of the delta error
)


@dataclass
class Candidate:
    """One proposal, positive or negative, with its features."""

    season: str
    era: str
    team_a: str
    team_b: str
    is_real: bool
    features: dict[str, float] = field(default_factory=dict)


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def team_book(season: str) -> tuple[dict[str, int], dict[str, int], dict[str, float]]:
    """Payroll, roster count and guaranteed share per team."""
    payroll: dict[str, int] = {}
    roster: dict[str, int] = {}
    guaranteed: dict[str, float] = {}
    for row in _rows(SNAPSHOTS / f"bbref-{season}" / "contracts.csv"):
        team = row["team_id"]
        salary = int(row["salary"])
        payroll[team] = payroll.get(team, 0) + salary
        roster[team] = roster.get(team, 0) + 1
        try:
            guaranteed[team] = guaranteed.get(team, 0.0) + int(row["guaranteed"] or 0)
        except (TypeError, ValueError):
            pass
    return payroll, roster, guaranteed


def extract(
    season: str,
    team_a: str,
    team_b: str,
    *,
    payroll: dict[str, int],
    roster: dict[str, int],
    standings=None,
    dispositions=None,
    values: dict[str, float] | None = None,
    moving_a: tuple[str, ...] = (),
    moving_b: tuple[str, ...] = (),
) -> dict[str, float]:
    """Features for one candidate pair. Pure; no model, no network."""
    import math

    values = values or {}
    pay_a, pay_b = payroll.get(team_a, 0), payroll.get(team_b, 0)
    out_v = sum(values.get(p, 0.0) for p in moving_a)
    in_v = sum(values.get(p, 0.0) for p in moving_b)

    features: dict[str, float] = {
        "salary_magnitude": math.log1p(max(pay_a, pay_b)),
        "salary_similarity": (
            1 - abs(pay_a - pay_b) / max(pay_a, pay_b) if max(pay_a, pay_b) else 0.0
        ),
        "roster_slot_distance": abs(roster.get(team_a, 0) - roster.get(team_b, 0)),
        "value_moving": out_v + in_v,
        "value_gap": abs(out_v - in_v),
    }
    if standings:
        a, b = standings.get(team_a), standings.get(team_b)
        features["record_gap"] = (
            abs(a.win_pct - b.win_pct) if a and b else 0.0
        )
    if dispositions:
        rank = {"seller": -1.0, "ambiguous": 0.0, "buyer": 1.0}
        da = dispositions.get(team_a)
        db = dispositions.get(team_b)
        features["disposition_pair"] = (
            rank.get(da.side, 0.0) - rank.get(db.side, 0.0) if da and db else 0.0
        )
    return features


def split_by_season(candidates: list[Candidate], test_seasons: set[str]):
    """Train/test split on whole seasons.

    Never within a season: a real trade's counterparty pair leaks across the
    14-day window, so a within-season split trains on the answer.
    """
    train = [c for c in candidates if c.season not in test_seasons]
    test = [c for c in candidates if c.season in test_seasons]
    return train, test


def precision_at_k(ranked: list[Candidate], k: int) -> float:
    head = ranked[:k]
    return sum(1 for c in head if c.is_real) / len(head) if head else 0.0


def recall_at_all(candidates: list[Candidate], real_pairs: set) -> float:
    """Did the enumerator retrieve the real trade at all? Its job, not the ranker's."""
    if not real_pairs:
        return 0.0
    found = {frozenset((c.team_a, c.team_b)) for c in candidates if c.is_real}
    return len(found & real_pairs) / len(real_pairs)


def permutation_null(
    candidates: list[Candidate],
    k: int,
    trials: int = 20_000,
    seed: int = 20260801,
) -> tuple[float, float]:
    """(mean precision@k under shuffled labels, p-value for the observed).

    Shuffles *which candidates are real*, preserving how many there are and the
    correlation structure of the proposals themselves. A binomial would treat
    each proposal as an independent draw, which they are not: the enumerator
    proposes systematically, so several proposals for one team pair hit or miss
    together and significance comes out overstated.
    """
    observed = precision_at_k(candidates, k)
    labels = [c.is_real for c in candidates]
    rng = random.Random(seed)
    scores = []
    for _ in range(trials):
        rng.shuffle(labels)
        scores.append(sum(labels[:k]) / k if k else 0.0)
    mean = sum(scores) / len(scores)
    at_or_above = sum(1 for s in scores if s >= observed) / len(scores)
    return mean, at_or_above


def report_line(label: str, observed: float, null: float, p: float) -> str:
    """Every figure as observed, null, ratio and p. Never observed alone."""
    ratio = observed / null if null else float("inf")
    return (
        f"  {label:<16} {observed * 100:>6.2f}%   null {null * 100:>5.2f}%   "
        f"{ratio:>5.1f}x   p={p:.3f}"
    )


# ---------------------------------------------------------------------------
# Proposal-level permutation, with two nulls.
# ---------------------------------------------------------------------------

def team_trade_frequency(seasons: list[str]) -> dict[str, int]:
    """How often each team appears in a real trade. The degree-preserving null.

    A planner that learned only *which teams are active* — sellers trade, good
    teams stand pat — would beat a uniform null without knowing anything about
    who trades with whom. Weighting the null by this frequency takes that away
    and leaves only the pairing.
    """
    from mironba.sim.deadline import actual_deadline_trades

    freq: dict[str, int] = {}
    for season in seasons:
        for trade in actual_deadline_trades(season):
            for team in trade.teams:
                freq[team] = freq.get(team, 0) + 1
    return freq


def _draw_qualifying(universe, k, rng, weights=None):
    if not weights:
        return set(rng.sample(universe, min(k, len(universe))))
    chosen: set = set()
    pool = list(universe)
    w = [weights.get(pair, 1e-9) for pair in pool]
    while len(chosen) < min(k, len(pool)):
        pick = rng.choices(pool, weights=w, k=1)[0]
        chosen.add(pick)
    return chosen


def proposal_level_null(
    per_season: list[dict],
    *,
    degree_weights: dict[str, int] | None = None,
    trials: int = 4000,
    seed: int = 20260801,
) -> dict:
    """Redraw which pairs qualify; score every proposal against them.

    ``per_season`` items need ``pairs`` (the multiset of team pairs, one entry
    per proposal), ``n_qualifying`` and ``proposed``.

    Proposals sharing a team pair hit or miss **together**, which is the whole
    point: instance #12 was a null that computed each season's *expectation*
    instead of drawing outcomes, so it had almost no variance and returned
    p<0.0001 for a 1.41x effect. ``spread`` is returned so that cannot recur
    silently.
    """
    import random
    from itertools import combinations

    rng = random.Random(seed)
    universe = [frozenset(c) for c in combinations(range(30), 2)]
    # The universe is pairs of integers 0-29; degree_weights is keyed by team
    # abbreviation. Mapping between them is required, and its absence is not
    # visible at runtime - the weights silently all become 1 and the
    # "degree-preserving" null is the uniform null wearing a label. Caught
    # because both nulls returned byte-identical figures.
    weights = None
    if degree_weights:
        ordered = sorted(degree_weights)
        if len(ordered) < 2:
            raise ValueError(
                "degree_weights needs at least two teams; with fewer, the "
                "weighted null degenerates to the uniform one silently."
            )
        index = {i: ordered[i % len(ordered)] for i in range(30)}
        weights = {
            pair: degree_weights.get(index[a], 1) * degree_weights.get(index[b], 1)
            for pair in universe
            for a, b in [tuple(pair)]
        }
        if len(set(weights.values())) == 1:
            raise ValueError(
                "every pair weight is identical - this is the uniform null, "
                "not a degree-preserving one. Refusing to report it as the latter."
            )

    total_proposals = sum(s["proposed"] for s in per_season)
    scores = []
    for _ in range(trials):
        hits = 0
        for season in per_season:
            qualifying = _draw_qualifying(
                universe, season["n_qualifying"], rng, weights
            )
            # Map this season's real pairs onto the drawn universe positionally,
            # preserving how many proposals sit on each pair.
            slots = list(universe)
            rng.shuffle(slots)
            assignment = {p: slots[i % len(slots)] for i, p in enumerate(season["pairs"])}
            hits += sum(1 for p in season["pairs"] if assignment[p] in qualifying)
        scores.append(hits / total_proposals if total_proposals else 0.0)

    mean = sum(scores) / len(scores)
    spread = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
    return {"mean": mean, "sd": spread, "scores": scores}


def p_value(scores: list[float], observed: float) -> float:
    return sum(1 for s in scores if s >= observed) / len(scores)


def normalized_headroom(observed: float, null: float) -> float:
    """(observed - null) / (1 - null). How much of the *available* gap was closed.

    A ratio flatters a small null: 1.41x sounds larger than moving 2.58% to
    3.64% actually is. This says what fraction of the distance to perfection
    was covered, which for these figures is about 1%.
    """
    return (observed - null) / (1 - null) if null < 1 else 0.0
