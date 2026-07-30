"""The three-valued verdict, and base-year-compensation detection.

The point of `UNDETERMINED` is that a caller cannot ignore it. These tests hold
that property in place, because the failure it prevents — a BYC trade silently
scored as rejected during an M4 backtest — is invisible when it happens.
"""

from __future__ import annotations

import pytest
from conftest import build_trade, player, team

from mironba.rules.trade_validator import (
    CashAsset,
    ReSignStatus,
    Rule,
    Severity,
    Verdict,
    VerdictUndetermined,
    summarize,
    validate_trade,
)

UNDER_CAP = 145_000_000
OVER_CAP = 180_000_000
SECOND_APRON = 210_000_000


def byc_trade(*, sender_salary: int = OVER_CAP, **player_kwargs):
    return build_trade(
        teams=(team("BOS", sender_salary), team("DAL", OVER_CAP)),
        players=(
            player(
                "Re-signed Vet",
                20_000_000,
                "BOS",
                "DAL",
                re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                **player_kwargs,
            ),
            player("Return", 20_000_000, "DAL", "BOS"),
        ),
    )


class TestVerdictType:
    def test_clean_trade_is_approved(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.APPROVED
        assert result.legal is True

    def test_illegal_trade_is_rejected(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 25_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.REJECTED
        assert result.legal is False

    def test_undetermined_is_neither(self):
        result = validate_trade(byc_trade())
        assert result.verdict is Verdict.UNDETERMINED
        assert result.verdict is not Verdict.APPROVED
        assert result.verdict is not Verdict.REJECTED

    def test_legal_raises_rather_than_guessing(self):
        """A caller who has not thought about this gets an exception, not False."""
        result = validate_trade(byc_trade())
        with pytest.raises(VerdictUndetermined) as excinfo:
            _ = result.legal
        assert "base-year" in str(excinfo.value).lower()
        assert "undetermined" in str(excinfo.value).lower()

    def test_a_caller_that_handles_it_can_read_the_reason(self):
        result = validate_trade(byc_trade())
        reasons = result.undetermined()
        assert len(reasons) == 1
        assert reasons[0].rule == Rule.BASE_YEAR_COMPENSATION
        assert reasons[0].severity is Severity.UNDETERMINED
        assert reasons[0].team == "BOS"

    def test_definite_error_outranks_undetermined(self):
        """BYC only lowers outgoing salary, so it cannot rescue a failed match."""
        trade = build_trade(
            teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
            players=(
                player(
                    "Re-signed Vet",
                    20_000_000,
                    "BOS",
                    "DAL",
                    re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                ),
                player("Return", 25_000_000, "DAL", "BOS"),
            ),
        )
        result = validate_trade(trade)
        assert result.verdict is Verdict.REJECTED
        assert result.undetermined()  # the flag is still recorded
        assert result.legal is False  # but the answer is definite

    def test_summarize_reports_the_verdict_name(self):
        assert "UNDETERMINED" in summarize(validate_trade(byc_trade()))


class TestBaseYearCompensationDetection:
    def test_under_cap_team_cannot_trigger_byc(self):
        """BYC only arises for a team over the cap when it re-signed the player."""
        result = validate_trade(byc_trade(sender_salary=UNDER_CAP))
        assert result.verdict is Verdict.APPROVED

    def test_not_re_signed_is_not_flagged(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Ordinary Vet", 20_000_000, "BOS", "DAL"),
                    player("Return", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.APPROVED

    def test_unknown_re_sign_status_is_flagged(self):
        """Missing data is not evidence of absence."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player(
                        "Mystery Vet",
                        20_000_000,
                        "BOS",
                        "DAL",
                        re_sign_status=ReSignStatus.UNKNOWN,
                    ),
                    player("Return", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.UNDETERMINED
        assert "do not know" in result.undetermined()[0].message

    def test_small_raise_rules_byc_out(self):
        """BYC needs a raise over 20%; 20,000,000 on 18,000,000 is 11.1%."""
        result = validate_trade(byc_trade(previous_salary=18_000_000))
        assert result.verdict is Verdict.APPROVED

    def test_large_raise_stays_flagged(self):
        """20,000,000 on 10,000,000 is a 100% raise — squarely in BYC territory."""
        result = validate_trade(byc_trade(previous_salary=10_000_000))
        assert result.verdict is Verdict.UNDETERMINED

    def test_raise_of_exactly_twenty_percent_rules_byc_out(self):
        """The threshold is *more than* 20%, so exactly 20% is clear."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player(
                        "Re-signed Vet",
                        12_000_000,
                        "BOS",
                        "DAL",
                        re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                        previous_salary=10_000_000,
                    ),
                    player("Return", 12_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.APPROVED

    def test_a_dollar_over_twenty_percent_is_flagged(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player(
                        "Re-signed Vet",
                        12_000_001,
                        "BOS",
                        "DAL",
                        re_sign_status=ReSignStatus.RE_SIGNED_BIRD,
                        previous_salary=10_000_000,
                    ),
                    player("Return", 12_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.verdict is Verdict.UNDETERMINED

    def test_explicit_match_value_resolves_the_flag(self):
        """Supplying the number asserts the caller has done the BYC math."""
        result = validate_trade(byc_trade(outgoing_match_value=15_000_000))
        assert result.verdict is not Verdict.UNDETERMINED

    def test_only_the_sending_team_is_flagged(self):
        """BYC changes outgoing match value; it does nothing to the receiver."""
        result = validate_trade(byc_trade())
        assert {f.team for f in result.undetermined()} == {"BOS"}

    def test_detection_never_computes_a_byc_value(self):
        """Detect only. The match value used must remain the plain cap hit."""
        result = validate_trade(byc_trade())
        assert result.per_team["BOS"].match.outgoing == 20_000_000


class TestCashProhibitionIsNotALimit:
    def test_second_apron_ban_fires_on_any_amount(self):
        """Well under the annual limit, and still illegal."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                cash=(CashAsset("BOS", "DAL", 250_000),),
            )
        )
        assert result.verdict is Verdict.REJECTED
        rules = {f.rule for f in result.errors()}
        assert Rule.CASH_SECOND_APRON in rules
        assert Rule.CASH_LIMIT not in rules  # not a limit breach — a ban

    def test_the_finding_says_it_is_a_prohibition(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                cash=(CashAsset("BOS", "DAL", 1_000_000),),
            )
        )
        finding = next(f for f in result.errors() if f.rule == Rule.CASH_SECOND_APRON)
        assert finding.detail["prohibition"] is True
        assert "prohibition" in finding.message

    def test_second_apron_team_may_still_receive_cash(self):
        """The ban is on sending. Receiving is not restricted."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                cash=(CashAsset("DAL", "BOS", 1_000_000),),
            )
        )
        assert result.verdict is Verdict.APPROVED

    def test_cash_below_the_minimum_is_rejected(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                cash=(CashAsset("BOS", "DAL", 50_000),),
            )
        )
        assert Rule.CASH_MINIMUM in {f.rule for f in result.errors()}
