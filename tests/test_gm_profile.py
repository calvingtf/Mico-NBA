"""Revealed disposition: sourced tenures, pre-date derivation, gated entry."""

from __future__ import annotations

from datetime import date

from mironba.models.gm_profile import (
    MIN_SEASONS,
    PARAMETERS,
    Profile,
    _seasons_before,
    coverage,
    load_tenures,
    profile,
    to_persona,
)


class TestTheTenureTableIsSourcedOrAbsent:
    def test_every_row_has_source_url_and_retrieval(self):
        rows = load_tenures()
        assert len(rows) == 56, "30 current + 25 sourced predecessors + 1 skip"
        for row in rows:
            assert row["source"] and row["url"].startswith("http")
            assert row["retrieved"], "a sourced row states when it was read"

    def test_unattributable_seasons_are_reported_not_guessed(self):
        cov = coverage()
        assert len(cov["attributable"]) + len(cov["unattributable"]) == 300
        assert len(cov["attributable"]) == 255
        mn = [s for t, s in cov["unattributable"] if t == "MIN"]
        assert len(mn) == 6, "MIN pre-Connelly seasons stay out, undated"


class TestStrictlyPreDate:
    def test_a_july_date_admits_the_season_that_just_ended(self):
        assert "2025-26" in _seasons_before(date(2026, 7, 6))

    def test_an_in_season_date_does_not_admit_the_running_season(self):
        assert "2025-26" not in _seasons_before(date(2026, 2, 1))
        assert "2024-25" in _seasons_before(date(2026, 2, 1))

    def test_an_earlier_as_of_uses_strictly_fewer_seasons(self):
        early = profile("OKC", date(2019, 8, 1))
        late = profile("OKC", date(2025, 8, 1))
        assert set(early.seasons) < set(late.seasons)
        assert max(early.seasons) <= "2018-19"

    def test_the_derivation_is_registered(self):
        from mironba.sim.league import DERIVED_FACTS

        entry = DERIVED_FACTS["gm_profile"]
        assert entry["freeze_computable"] is True
        assert "aggregation_rate" in entry["note"]


class TestUnknownFallsBackLoudly:
    def test_a_2025_hire_is_unknown_not_defaulted_silently(self):
        prof = profile("DEN", date(2026, 7, 6))  # Tenzer 2025: one season
        assert prof.status == "UNKNOWN"
        assert prof.values == {}
        assert len(prof.seasons) < MIN_SEASONS

    def test_unknown_maps_to_a_persona_that_says_so(self):
        persona = to_persona(profile("DEN", date(2026, 7, 6)), {})
        assert "UNKNOWN" in persona.label or "league-average" in persona.label
        assert persona.asset_hoarding == 0.5


class TestTheValidationGate:
    def _extreme(self):
        return Profile("XXX", "Test GM", date(2026, 7, 6), ("2023-24", "2024-25"),
                       "OK", {"aggregation_rate": 0.9})

    def test_a_parameter_that_failed_its_null_does_not_enter_the_sim(self):
        persona = to_persona(self._extreme(), {"aggregation_rate": 0.3})
        assert persona.asset_hoarding == 0.5, (
            "aggregation failed its out-of-sample null; the mapping must not "
            "differentiate on it without an explicit probe flag"
        )
        assert "failed its null" in persona.label

    def test_the_wiring_probe_is_explicit_and_moves_the_parameter(self):
        persona = to_persona(self._extreme(), {"aggregation_rate": 0.3},
                             force_unvalidated=True)
        assert persona.asset_hoarding == 0.2


class TestWhatThisIsNamed:
    def test_revealed_disposition_not_belief_modelling(self):
        import mironba.models.gm_profile as gm_profile

        doc = gm_profile.__doc__
        assert "REVEALED DISPOSITION" in doc
        assert "it thinks, wants, or will do" in doc

    def test_every_declared_parameter_is_a_key_of_an_ok_profile(self):
        prof = profile("OKC", date(2026, 7, 6))
        assert prof.status == "OK"
        assert set(prof.values) == set(PARAMETERS)


class TestTheWiring:
    def test_low_spend_caps_at_the_first_apron(self):
        from mironba.rules.constants import environment_for
        from mironba.sim.league import signing_ceiling

        env = environment_for("2026-27")
        assert signing_ceiling("AAA", env, None) == env.second_apron
        assert signing_ceiling("AAA", env,
                               {"AAA": {"low_spend": True}}) == env.first_apron
        assert signing_ceiling("AAA", env,
                               {"AAA": {"low_spend": False}}) == env.second_apron

    def test_below_average_trade_rate_gates_the_cascade(self):
        from mironba.sim.cascade import gate_by_trade_rate

        assert gate_by_trade_rate("AAA", None)
        assert not gate_by_trade_rate(
            "AAA", {"AAA": {"below_avg_trade_rate": True}})

    def test_unknown_profiles_behave_like_the_uniform_arm(self):
        from datetime import date

        from mironba.models.gm_profile import profile, to_behavior

        prof = profile("DEN", date(2026, 7, 6))  # 2025 hire -> UNKNOWN
        behavior = to_behavior({"DEN": prof}, {"spend_level": 1.0,
                                               "trade_rate": 5.0})
        assert behavior["DEN"] == {"low_spend": False,
                                   "below_avg_trade_rate": False}

    def test_deadline_share_is_declared_not_wirable(self):
        from mironba.models.gm_profile import to_behavior

        assert "NOT WIRABLE" in to_behavior.__doc__
        assert "no in-world clock" in to_behavior.__doc__
