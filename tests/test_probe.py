"""Schema-enforcement probing, offline.

The probe's own logic is tested here. What a real server does is a live
measurement: `python -m mironba.sim.bench --probe`.
"""

from __future__ import annotations

from mironba.llm.probe import (
    FLAT_SCHEMA,
    REF_SCHEMA,
    ProbeOutcome,
    _conforms,
    inline_refs,
)


class TestConformance:
    def test_a_conforming_object_passes(self):
        assert _conforms('{"wavelength_nm": 450, "verdict": "scattering"}', FLAT_SCHEMA)

    def test_prose_fails(self):
        assert not _conforms("The sky appears blue due to Rayleigh scattering.", FLAT_SCHEMA)

    def test_a_missing_required_field_fails(self):
        assert not _conforms('{"wavelength_nm": 450}', FLAT_SCHEMA)

    def test_a_value_outside_the_enum_fails(self):
        assert not _conforms(
            '{"wavelength_nm": 450, "verdict": "refraction"}', FLAT_SCHEMA
        )

    def test_an_extra_field_fails_when_additional_properties_is_false(self):
        assert not _conforms(
            '{"wavelength_nm": 450, "verdict": "scattering", "extra": 1}', FLAT_SCHEMA
        )

    def test_a_wrong_type_fails(self):
        assert not _conforms(
            '{"wavelength_nm": "blue", "verdict": "scattering"}', FLAT_SCHEMA
        )


class TestInlineRefs:
    def test_defs_are_removed(self):
        inlined = inline_refs(REF_SCHEMA)
        assert "$defs" not in inlined

    def test_the_referenced_enum_survives_inlining(self):
        inlined = inline_refs(REF_SCHEMA)
        assert inlined["properties"]["verdict"]["enum"] == ["scattering", "absorption"]

    def test_the_inlined_shape_still_validates_the_same_objects(self):
        inlined = inline_refs(REF_SCHEMA)
        good = '{"wavelength_nm": 450, "verdict": "scattering"}'
        bad = '{"wavelength_nm": 450, "verdict": "refraction"}'
        assert _conforms(good, inlined)
        assert not _conforms(bad, inlined)


class TestEnforcementIsAllOrNothing:
    def test_partial_conformance_is_not_enforcement(self):
        """A grammar that lets one reply through in five is not a grammar.

        Calling 4/5 "mostly enforced" is how a defence gets credited for work
        the repair retry is actually doing.
        """
        outcome = ProbeOutcome("flat", trials=5, conformed=4, prose=1)
        assert not outcome.enforced

    def test_full_conformance_is(self):
        assert ProbeOutcome("flat", trials=3, conformed=3).enforced

    def test_zero_trials_is_not_enforcement(self):
        """No evidence is not evidence of enforcement."""
        assert not ProbeOutcome("flat", trials=0, conformed=0).enforced
