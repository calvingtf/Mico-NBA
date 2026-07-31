"""Replay fixtures through the validator, and enforce the coverage matrix.

The charter's "~30 real trades" was a proxy for the thing that actually
matters: every rule path having at least one verified fixture. The matrix in
docs/milestones.md holds the matrix; that table is the real gate.

The gate is split by what kind of question a row asks.
`test_formula_coverage_is_complete` is the M0 gate proper and must be green:
those rows are answerable with a synthetic fixture on the boundary. REALITY
rows need per-team apron salary on the trade date and BYC status, which no
available source publishes, so they are deferred to M4 — but
`test_deferred_reality_cells_are_declared` and
`test_formula_rows_cannot_be_deferred` keep "deferred" from becoming a place
to hide work.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from coverage_matrix import deferred_row_ids, matrix_rows, unchecked

from mironba.rules.cap import ApronTier, TradeException
from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    CashAsset,
    PlayerAsset,
    ReSignStatus,
    TeamTradeState,
    Trade,
    Verdict,
    summarize,
    validate_trade,
)

FIXTURES = Path(__file__).parent / "fixtures" / "real_trades.yaml"

TRADES = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["trades"]
REAL = [f for f in TRADES if f.get("kind") == "real"]


def representative_salary(tier: str, season: str) -> int:
    """A team salary that sits squarely inside ``tier``.

    Midpoints, not edges: a fixture should not flip verdict because a team was
    a dollar either side of a line it was nowhere near in reality.
    """
    env = environment_for(season)
    match tier:
        case "under_cap":
            return env.salary_cap - 25_000_000
        case "over_cap":
            return (env.salary_cap + env.first_apron) // 2
        case "first_apron":
            return (env.first_apron + env.second_apron) // 2
        case "second_apron":
            return env.second_apron + 8_000_000
        case _:
            raise ValueError(f"unknown tier {tier!r}")


def build(fixture: dict) -> Trade:
    season = fixture["season"]
    trade_date = date.fromisoformat(str(fixture["date"]))

    teams = tuple(
        TeamTradeState(
            team_id=team_id,
            team_salary=spec.get("salary") or representative_salary(spec["tier"], season),
            roster_count=spec.get("roster", 15),
            trade_exceptions=tuple(
                TradeException(
                    amount=t["amount"],
                    created_season=t["created_season"],
                    label=t.get("label", ""),
                    from_sign_and_trade=t.get("from_sign_and_trade", False),
                )
                for t in spec.get("trade_exceptions", [])
            ),
            cash_sent_this_year=spec.get("cash_sent_this_year", 0),
        )
        for team_id, spec in fixture["teams"].items()
    )

    players = tuple(
        PlayerAsset(
            player_id=f"{fixture['id']}:{i}",
            name=p["name"],
            salary=p["salary"],
            from_team=p["from"],
            to_team=p["to"],
            sign_and_trade=p.get("sign_and_trade", False),
            re_sign_status=ReSignStatus(p.get("re_sign_status", "not_re_signed")),
            previous_salary=p.get("previous_salary"),
            years_of_service=p.get("years_of_service"),
            acquired_via_trade_on=(
                trade_date - timedelta(days=p["acquired_days_ago"])
                if "acquired_days_ago" in p
                else None
            ),
        )
        for i, p in enumerate(fixture["players"])
    )

    cash = tuple(
        CashAsset(from_team=c["from"], to_team=c["to"], amount=c["amount"])
        for c in fixture.get("cash", [])
    )

    return Trade(
        season=season,
        trade_date=trade_date,
        teams=teams,
        players=players,
        cash=cash,
        label=fixture["label"],
    )


def _ids(fixtures):
    return [f["id"] for f in fixtures]


@pytest.mark.parametrize("fixture", TRADES, ids=_ids(TRADES))
def test_fixture_is_well_formed(fixture):
    """Schema guard, so a typo in the YAML fails loudly rather than silently."""
    for key in ("id", "label", "kind", "season", "date", "expected_verdict", "teams", "players"):
        assert key in fixture, f"{fixture.get('id')} is missing {key!r}"
    assert fixture["kind"] in {"real", "counterfactual", "synthetic"}
    assert fixture["expected_verdict"] in {"approved", "rejected", "undetermined"}

    team_ids = set(fixture["teams"])
    assert len(team_ids) >= 2
    for p in fixture["players"]:
        assert p["from"] in team_ids, f"{p['name']} comes from a non-participant"
        assert p["to"] in team_ids, f"{p['name']} goes to a non-participant"
        assert p["salary"] > 0
    for spec in fixture["teams"].values():
        assert "salary" in spec or "tier" in spec

    # A real trade cannot be verified until its salaries are sourced, and a
    # counterfactual is verified by construction. Anything else is a mistake.
    if fixture["kind"] in {"counterfactual", "synthetic"}:
        assert fixture.get("verified") is True, "constructed fixtures are rule-derived"
    if fixture["kind"] == "synthetic":
        assert fixture.get("edge_justification"), "a synthetic fixture must justify its edge"


@pytest.mark.parametrize("fixture", TRADES, ids=_ids(TRADES))
def test_verdict_matches_expectation(fixture):
    result = validate_trade(build(fixture))
    expected = Verdict(fixture["expected_verdict"])

    assert result.verdict is expected, (
        f"\n{fixture['label']} ({fixture['season']})\n"
        f"expected {expected.name}, got {result.verdict.name}\n"
        f"kind={fixture['kind']} verified={fixture.get('verified', False)} "
        f"simplified={fixture.get('simplified', False)}\n"
        f"{summarize(result)}\n"
    )

    for rule in fixture.get("expected_errors", []):
        assert rule in {f.rule for f in result.errors()}, (
            f"{fixture['id']}: expected error {rule}, got {summarize(result)}"
        )
    for rule in fixture.get("expected_undetermined", []):
        assert rule in {f.rule for f in result.undetermined()}, (
            f"{fixture['id']}: expected undetermined {rule}, got {summarize(result)}"
        )


@pytest.mark.parametrize("fixture", REAL, ids=_ids(REAL))
def test_real_trades_clear_the_limit_with_margin(fixture):
    """A real trade should not sit within a rounding error of the limit.

    Every real fixture uses recalled salaries. If one only clears by a few
    thousand dollars, the verdict is an artefact of the recalled number rather
    than a property of the trade, and the fixture is not evidence of anything.
    The Towns deal was reported as tight, so it gets a smaller floor.
    """
    result = validate_trade(build(fixture))
    tight = {"towns-knicks-2024"}
    floor = 100_000 if fixture["id"] in tight else 1_000_000

    for team_id, outcome in result.per_team.items():
        assert outcome.match.headroom >= floor, (
            f"{fixture['id']}: {team_id} clears by only "
            f"${outcome.match.headroom:,}, too close to trust recalled salaries"
        )


def test_apron_paths_are_exercised():
    """Guard against a fixture set that only ever tests the easy path."""
    tiers = set()
    for fixture in TRADES:
        result = validate_trade(build(fixture))
        tiers.update(o.tier_after for o in result.per_team.values())
    assert ApronTier.SECOND_APRON in tiers
    assert ApronTier.FIRST_APRON in tiers


# --------------------------------------------------------------------------
# The coverage matrix
# --------------------------------------------------------------------------

#: Which fixture kind may satisfy which cell.
CELL_REQUIREMENTS = {
    ("FORMULA", "positive"): "synthetic",
    ("REALITY", "positive"): "real",
    ("FORMULA", "negative"): "counterfactual",
    ("REALITY", "negative"): "counterfactual",
}


def _fixture_ids_covering(row_id: str, kind: str) -> set[str]:
    return {
        f["id"]
        for f in TRADES
        if f.get("covers") == row_id and f.get("verified") and f.get("kind") == kind
    }


def test_matrix_rows_are_unique():
    rows = matrix_rows()
    ids = [r["row_id"] for r in rows]
    assert len(ids) == len(set(ids)), f"duplicate matrix row ids: {ids}"


@pytest.mark.parametrize("row", matrix_rows(), ids=lambda r: r["row_id"])
def test_checked_matrix_cells_have_a_verified_fixture(row):
    """A checked box must be backed by a verified fixture of the right kind.

    Without this, checking a row is just typing an x. The kind matters: a
    REALITY row checked with a synthetic fixture would be testing our reading
    of the rule against itself.
    """
    for cell in ("positive", "negative"):
        if row[cell] != "[x]":
            continue
        required = CELL_REQUIREMENTS[(row["kind"], cell)]
        found = _fixture_ids_covering(row["row_id"], required)
        assert found, (
            f"matrix row {row['row_id']!r} ({row['kind']}) claims a {cell} "
            f"fixture, but no verified {required!r} fixture in real_trades.yaml "
            f"has covers: {row['row_id']}"
        )


@pytest.mark.parametrize("row", matrix_rows(), ids=lambda r: r["row_id"])
def test_formula_positives_justify_their_edge(row):
    """A synthetic fixture has to show its arithmetic.

    A synthetic positive is only worth anything if it sits on the boundary. The
    justification is where that claim is written down and checkable by a human
    who did not build it.
    """
    if row["kind"] != "FORMULA" or row["positive"] != "[x]":
        return
    for fixture in TRADES:
        if fixture.get("covers") == row["row_id"] and fixture.get("kind") == "synthetic":
            justification = fixture.get("edge_justification", "")
            assert len(justification.split()) >= 12, (
                f"{fixture['id']}: edge_justification must state the arithmetic "
                f"that makes this the boundary value"
            )
            assert any(ch.isdigit() for ch in justification), (
                f"{fixture['id']}: edge_justification names no numbers"
            )


def test_formula_coverage_is_complete():
    """**The M0 gate.** Every FORMULA cell must be checked.

    A FORMULA row asks whether our arithmetic is right at a boundary, and a
    synthetic fixture answers it outright — no external data, no judgement
    call. So there is never a good reason for one of these to be open, and an
    unchecked FORMULA cell fails the suite.

    This is the half of the old combined gate that could actually be closed.
    The other half is not a safety property; see below.
    """
    missing = unchecked("FORMULA")
    if missing:
        listing = "\n".join(
            f"  {row_id:<28} {cell:<9} ({label})" for row_id, cell, label in missing
        )
        pytest.fail(
            f"M0 FORMULA coverage incomplete — {len(missing)} unchecked cell(s):\n"
            f"{listing}\n\n"
            "A FORMULA cell needs a synthetic fixture placed exactly on the "
            "boundary, with an edge_justification showing the arithmetic, "
            "paired with a counterfactual one dollar past it. No external "
            "data required — this is closeable now."
        )


def test_deferred_reality_cells_are_declared():
    """An open REALITY cell must be written down as deferred, with a reason.

    REALITY rows are deferred rather than failed because they need per-team
    apron salary on the trade date and BYC status, and no source we have
    publishes either. That is a real constraint, not an excuse — so the escape
    hatch is narrow: the row id has to appear in the milestone record's deferred
    register. An open cell nobody declared still fails.
    """
    declared = deferred_row_ids()
    undeclared = sorted(
        {row_id for row_id, _, _ in unchecked("REALITY")} - declared
    )
    assert not undeclared, (
        f"{len(undeclared)} REALITY row(s) unchecked but not declared under "
        f"'### Deferred to M4' in docs/milestones.md: {', '.join(undeclared)}. "
        "Either check the cell with a verified real fixture, or add the row "
        "to the deferred register saying what data it is waiting on."
    )


def test_formula_rows_cannot_be_deferred():
    """The deferred register may name REALITY rows only.

    Without this, the gate has a trapdoor: move a failing FORMULA row into the
    deferred list and M0 goes green without the arithmetic ever being checked.
    Deferral is a statement about missing *evidence*, never about missing work.
    """
    formula_ids = {r["row_id"] for r in matrix_rows() if r["kind"] == "FORMULA"}
    smuggled = sorted(deferred_row_ids() & formula_ids)
    assert not smuggled, (
        f"FORMULA row(s) in the deferred register: {', '.join(smuggled)}. "
        "A FORMULA row is closeable with a synthetic fixture and cannot be "
        "deferred — close it instead."
    )


def test_deferred_register_has_no_stale_entries():
    """A row that got checked must come out of the register.

    Otherwise the deferred count reported at the top of every run drifts
    upward from reality and stops being worth reading.
    """
    open_reality = {row_id for row_id, _, _ in unchecked("REALITY")}
    known = {r["row_id"] for r in matrix_rows()}
    stale = sorted(deferred_row_ids() - open_reality)
    assert not stale, (
        f"deferred register lists row(s) that are no longer open: "
        f"{', '.join(stale)}. "
        + ("Some are not matrix rows at all: "
           f"{', '.join(sorted(set(stale) - known))}. " if set(stale) - known else "")
        + "Remove them from '### Deferred to M4'."
    )
