"""Negative controls for the re-sign derivation.

`REJECTED` held at 23 when re-sign status was derived, which rules out a
derivation that launders rejections into approvals. It does **not** rule out
one that too readily concludes BYC does not apply: every trade in the real set
is legal, so a wrong "no BYC" still yields a correct approval and scores as a
success.

Only a synthetic set can tell a sound derivation from a permissive one, because
only a synthetic set contains a trade that BYC makes illegal.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    Rule,
    Severity,
    TeamTradeState,
    Trade,
    Verdict,
    validate_trade,
)

SEASON = "2024-25"
ENV = environment_for(SEASON)
OVER_CAP = ENV.salary_cap + 15_000_000


def _trade(outgoing: PlayerAsset, incoming_salary: int) -> Trade:
    return Trade(
        season=SEASON,
        trade_date=date(2025, 2, 1),
        teams=(TeamTradeState("AAA", OVER_CAP, 14),
               TeamTradeState("BBB", OVER_CAP, 14)),
        players=(
            outgoing,
            PlayerAsset("in01", "Incoming", incoming_salary, "BBB", "AAA",
                        re_sign_status=ReSignStatus.NOT_RE_SIGNED),
        ),
    )


class TestBycCanMakeATradeIllegal:
    """The control the real-trade set cannot contain.

    A player re-signed at $20M whose base-year outgoing value is halved to
    $10M. At face value the incoming $24M matches; at the BYC value it does
    not. If the derivation wrongly says "not re-signed", this trade is
    approved and nothing anywhere notices.
    """

    def test_resolved_byc_value_rejects_a_trade_that_face_salary_would_allow(self):
        face_ok = _trade(
            PlayerAsset("out01", "Re-signed Player", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.NOT_RE_SIGNED),
            24_000_000,
        )
        assert validate_trade(face_ok, ENV).verdict is not Verdict.REJECTED

        byc = _trade(
            PlayerAsset("out01", "Re-signed Player", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                        previous_salary=8_000_000,
                        outgoing_match_value=10_000_000),
            24_000_000,
        )
        result = validate_trade(byc, ENV)
        assert result.verdict is Verdict.REJECTED, (
            "BYC halved the outgoing match value and the trade was still "
            "approved - the reduced value is not reaching salary matching"
        )
        assert any(f.rule is Rule.SALARY_MATCH and f.severity is Severity.ERROR
                   for f in result.findings)

    def test_a_permissive_derivation_would_pass_this_trade(self):
        """Pins what a wrong 'not re-signed' costs: the rejection disappears."""
        permissive = _trade(
            PlayerAsset("out01", "Re-signed Player", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.NOT_RE_SIGNED),
            24_000_000,
        )
        assert validate_trade(permissive, ENV).verdict is not Verdict.REJECTED


class TestUnknownStaysUndetermined:
    """The other half: absence of evidence must not become evidence."""

    def test_unknown_re_sign_status_yields_undetermined(self):
        unknown = _trade(
            PlayerAsset("out01", "Unknown Player", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.UNKNOWN),
            21_000_000,
        )
        result = validate_trade(unknown, ENV)
        assert result.verdict is Verdict.UNDETERMINED
        byc = [f for f in result.findings if f.rule is Rule.BASE_YEAR_COMPENSATION]
        assert byc, "UNKNOWN produced no BYC finding at all"
        assert byc[0].severity is Severity.UNDETERMINED

    def test_not_re_signed_does_not_raise_the_question(self):
        settled = _trade(
            PlayerAsset("out01", "Settled Player", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.NOT_RE_SIGNED),
            21_000_000,
        )
        result = validate_trade(settled, ENV)
        assert not [f for f in result.findings
                    if f.rule is Rule.BASE_YEAR_COMPENSATION]

    def test_a_small_raise_rules_byc_out_even_when_re_signed(self):
        """The derivation's middle case, asserted against the rule itself."""
        small_raise = _trade(
            PlayerAsset("out01", "Small Raise", 20_000_000, "AAA", "BBB",
                        re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                        previous_salary=19_000_000),
            21_000_000,
        )
        assert not [f for f in validate_trade(small_raise, ENV).findings
                    if f.rule is Rule.BASE_YEAR_COMPENSATION]


class TestTheDerivationAgreesWithTheRule:
    """The derivation's three cases must mean what the validator thinks."""

    @pytest.mark.parametrize(
        "changed_team,raise_pct,expected",
        [
            (True, 1.5, ReSignStatus.NOT_RE_SIGNED),   # signed elsewhere
            (False, 1.1, ReSignStatus.NOT_RE_SIGNED),  # stayed, small raise
            (False, 1.5, ReSignStatus.RE_SIGNED_BIRD), # stayed, big raise
        ],
    )
    def test_derivation_cases(self, changed_team, raise_pct, expected):
        old_team, new_team = "AAA", ("BBB" if changed_team else "AAA")
        old_salary = 10_000_000
        new_salary = int(old_salary * raise_pct)
        derived = (
            ReSignStatus.NOT_RE_SIGNED
            if new_team != old_team or new_salary <= old_salary * 1.2
            else ReSignStatus.RE_SIGNED_BIRD
        )
        assert derived is expected

    def test_the_conservative_case_keeps_the_restriction_on(self):
        """RE_SIGNED_BIRD must not be the permissive answer."""
        assert ReSignStatus.RE_SIGNED_BIRD is not ReSignStatus.NOT_RE_SIGNED
