"""The cap environment is data, so it gets tested like data."""

from __future__ import annotations

import dataclasses

import pytest

from mironba.rules.constants import (
    CBA_2023,
    CONTESTED,
    PROVENANCE,
    CapEnvironment,
    environment_for,
    known_seasons,
)


def modern_seasons():
    """Seasons under the 2023 CBA.

    Several invariants below are era-specific and were written when every
    ingested season was a modern one. The second apron does not exist before
    2023-24, the expanded TPE does not exist at all (a flat $5M buffer applies
    instead), and the cash limit is not sourced. Applying those assertions to
    a 2019 environment tests the 2023 rulebook against the wrong league.
    """
    return [s for s in known_seasons() if environment_for(s).cba_era == CBA_2023]


@pytest.mark.parametrize("season", modern_seasons())
def test_thresholds_are_strictly_ordered(season):
    env = environment_for(season)
    assert (
        env.minimum_team_salary
        < env.salary_cap
        < env.tax_level
        < env.first_apron
        < env.second_apron
    )


@pytest.mark.parametrize("season", known_seasons())
def test_the_cap_and_tax_are_ordered_in_every_era(season):
    """What holds for all seasons. The aprons are deliberately excluded: they
    are inert placeholders pre-2023 and no rule of that era reads them."""
    env = environment_for(season)
    assert env.minimum_team_salary < env.salary_cap < env.tax_level


@pytest.mark.parametrize("season", known_seasons())
def test_minimum_team_salary_is_ninety_percent_of_cap(season):
    env = environment_for(season)
    # The league publishes both numbers; if our cap is right this must hold.
    assert env.minimum_team_salary == pytest.approx(env.salary_cap * 0.90, abs=1_000)


def test_apron_matching_tightened_after_2023_24():
    """The single most load-bearing season-varying rule.

    110% applied to apron teams in 2023-24 only. Any regression that makes this
    uniform silently approves trades the league would reject.
    """
    assert environment_for("2023-24").apron_match_pct == 110
    for season in modern_seasons():
        if season != "2023-24":
            assert environment_for(season).apron_match_pct == 100


def test_expanded_tpe_tracks_the_cap():
    """The published 2025-26 figure is the check on the scaling model."""
    assert environment_for("2023-24").expanded_tpe == 7_500_000
    assert environment_for("2025-26").expanded_tpe == 8_527_000

    base = environment_for("2023-24")
    for season in modern_seasons():
        env = environment_for(season)
        expected = base.expanded_tpe * env.salary_cap / base.salary_cap
        assert env.expanded_tpe == pytest.approx(expected, rel=0.002)


def test_every_field_has_provenance():
    """Reproducibility non-negotiable: no unattributed number.

    A new field on CapEnvironment must arrive with a source, or this fails.
    """
    # Module-level constant tables also carry provenance, so PROVENANCE is a
    # superset of the dataclass fields — but only by these declared names.
    MODULE_LEVEL = {"minimum_salary_scale"}

    fields = {f.name for f in dataclasses.fields(CapEnvironment)} - {"season"}
    documented = set(PROVENANCE)
    assert fields <= documented, (
        f"CapEnvironment fields without provenance: {sorted(fields - documented)}"
    )
    assert documented - fields <= MODULE_LEVEL, (
        f"stale PROVENANCE keys: {sorted(documented - fields - MODULE_LEVEL)}"
    )
    for name, (confidence, note) in PROVENANCE.items():
        assert confidence in {"verified", "derived", "unverified"}, name
        assert note.strip(), name


def test_unverified_constants_are_declared():
    """Pin the known-weak numbers so removing a caveat is a deliberate act.

    Currently empty: every field has been sourced. Adding an unsourced number
    fails here, which is the point — the charter treats an unattributed figure
    as worse than a gap.
    """
    unverified = {n for n, (c, _) in PROVENANCE.items() if c == "unverified"}
    assert unverified == set(), (
        f"unsourced constants present: {sorted(unverified)}. Source them or "
        f"state why they cannot be."
    )


def test_cash_limit_is_a_constant_ratio_of_the_cap():
    """Cross-check on four independently sourced figures.

    All four seasons land on 5.15% of that season's cap. That agreement is why
    the sourced numbers are trusted; an earlier revision assumed the cash limit
    tracked the expanded TPE and was wrong by $250K-$600K every season.
    """
    for season in modern_seasons():
        env = environment_for(season)
        assert env.cash_limit == pytest.approx(env.salary_cap * 0.0515, rel=0.001), season


def test_cash_limit_is_never_zero_for_any_season():
    """The second-apron cash ban must not be expressed as a limit of zero.

    A prohibition and a limit are different things: the ban is absolute and has
    its own rule id, while this number moves every year. If someone ever
    encodes the ban by zeroing this field, the ban silently becomes negotiable.

    Scoped to the 2023 era because the pre-2023 cash limit was never sourced.
    Those seasons carry 0, which reads as "not modelled" and is listed as such
    in ERA_COVERAGE - a different statement from "the limit is zero", and one
    the cash rule refuses to act on rather than treating as a ban.
    """
    for season in modern_seasons():
        assert environment_for(season).cash_limit > 0, season


def test_contested_readings_are_documented():
    """A rule we had to choose between readings on must say so, and say why."""
    assert CONTESTED, "expected at least the lower-bracket-boundary entry"
    for name, entry in CONTESTED.items():
        assert entry.question.strip(), name
        assert entry.adopted.strip(), name
        assert entry.rationale.strip(), name
        assert entry.alternative.strip(), name
        assert entry.impact_if_wrong.strip(), name
        assert entry.sources, name


def test_lower_bracket_boundary_resolution_is_recorded():
    entry = CONTESTED["lower_bracket_boundary"]
    assert "7,250,000" in entry.adopted
    assert "7,500,000" in entry.alternative
    # The direction of the risk is the part a future reader most needs.
    assert "reject" in entry.impact_if_wrong.lower()


def test_unknown_season_raises():
    with pytest.raises(KeyError, match="no cap environment"):
        environment_for("1998-99")
