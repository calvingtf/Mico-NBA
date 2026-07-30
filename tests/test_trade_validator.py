"""Trade legality edge cases.

Team salaries are chosen to land in a specific apron tier and stated inline, so
each test isolates one rule. 2025-26 reference points:

    cap 154,647,000 | tax 187,895,000 | apron1 195,945,000 | apron2 207,824,000
    expanded TPE 8,527,000 | apron matching 100% | floor 139,182,000
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import build_trade, error_rules, player, rules_fired, team

from mironba.rules.cap import ApronTier, TradeException
from mironba.rules.trade_validator import (
    CashAsset,
    PickAsset,
    Rule,
    Severity,
    earliest_trade_date,
    summarize,
    validate_trade,
)

UNDER_CAP = 145_000_000  # cap room 9,647,000; above the 139,182,000 floor
OVER_CAP = 180_000_000
FIRST_APRON = 199_000_000
SECOND_APRON = 210_000_000


class TestSalaryMatching:
    def test_straightforward_two_team_trade(self):
        # BOS: out 20,000,000 -> bracket limit 28,527,000. In 25,000,000. Legal.
        # DAL: out 25,000,000 -> bracket limit 33,527,000. In 20,000,000. Legal.
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 25_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()
        assert result.per_team["BOS"].match.headroom == 28_527_000 - 25_000_000

    def test_over_cap_team_cannot_exceed_its_bracket(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 29_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert not result.legal
        assert Rule.SALARY_MATCH in error_rules(result)
        finding = next(f for f in result.errors() if f.rule == Rule.SALARY_MATCH)
        assert finding.team == "BOS"
        assert finding.detail["over_by"] == 29_000_000 - 28_527_000

    def test_second_apron_team_capped_at_one_hundred_percent(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_001, "DAL", "BOS"),
                ),
            )
        )
        assert not result.legal
        assert result.per_team["BOS"].match.max_incoming == 20_000_000

    def test_second_apron_team_may_match_exactly(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()

    def test_apron_matching_was_looser_in_2023_24(self):
        """Same trade, different season, different verdict.

        This is the regression that matters most: 110% applied to apron teams
        in 2023-24 only. 2023-24 second apron is 182,794,000.
        """
        players = (
            player("Out East", 20_000_000, "BOS", "DAL"),
            player("Out West", 22_000_000, "DAL", "BOS"),
        )
        # 210,000,000 clears the second apron in both seasons (182,794,000 in
        # 2023-24, 207,824,000 in 2025-26), so only the percentage differs.
        teams = (team("BOS", SECOND_APRON), team("DAL", OVER_CAP))

        legal_then = validate_trade(
            build_trade(
                season="2023-24", trade_date=date(2024, 1, 20), teams=teams, players=players
            )
        )
        assert legal_then.legal, legal_then.explain()
        assert legal_then.per_team["BOS"].tier_after is ApronTier.SECOND_APRON

        illegal_now = validate_trade(build_trade(teams=teams, players=players))
        assert not illegal_now.legal

    def test_tier_is_decided_after_the_trade_not_before(self):
        """A team that ducks under the apron is not bound by apron matching.

        Starts at 199,000,000 (first apron), sends 28,000,000, takes back
        20,000,000 -> ends at 191,000,000, below the first apron. Apron
        matching would cap it at 28,000,000; the bracket allows 36,527,000.
        """
        result = validate_trade(
            build_trade(
                teams=(team("GSW", FIRST_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Big Deal", 28_000_000, "GSW", "DAL"),
                    player("Return", 20_000_000, "DAL", "GSW"),
                ),
            )
        )
        assert result.legal, result.explain()
        assert result.per_team["GSW"].tier_before is ApronTier.FIRST_APRON
        assert result.per_team["GSW"].tier_after is ApronTier.OVER_CAP
        assert result.per_team["GSW"].match.max_incoming == 36_527_000

    def test_under_cap_team_absorbs_into_room(self):
        # Room 9,647,000 + 0 outgoing + 250,000 cushion = 9,897,000.
        legal = validate_trade(
            build_trade(
                teams=(team("UTA", UNDER_CAP, roster=14), team("DAL", OVER_CAP)),
                players=(player("Absorbed", 9_000_000, "DAL", "UTA"),),
            )
        )
        assert legal.legal, legal.explain()

        illegal = validate_trade(
            build_trade(
                teams=(team("UTA", UNDER_CAP, roster=14), team("DAL", OVER_CAP)),
                players=(player("Too Big", 10_000_000, "DAL", "UTA"),),
            )
        )
        assert not illegal.legal
        assert illegal.per_team["UTA"].match.max_incoming == 9_897_000


class TestAggregation:
    def _two_for_one(self, salary: int, **team_kwargs):
        return build_trade(
            teams=(team("BOS", salary, **team_kwargs), team("DAL", OVER_CAP, roster=13)),
            players=(
                player("Piece A", 10_000_000, "BOS", "DAL"),
                player("Piece B", 12_000_000, "BOS", "DAL"),
                player("Star", 21_000_000, "DAL", "BOS"),
            ),
        )

    def test_below_the_apron_aggregation_is_allowed(self):
        result = validate_trade(self._two_for_one(OVER_CAP))
        assert result.legal, result.explain()

    def test_second_apron_team_cannot_aggregate(self):
        """Salary matching passes (21M <= 22M); aggregation is the blocker."""
        result = validate_trade(self._two_for_one(SECOND_APRON))
        assert not result.legal
        assert error_rules(result) == {Rule.AGGREGATION_SECOND_APRON}
        assert result.per_team["BOS"].match.legal

    def test_second_apron_team_may_send_two_if_matched_individually(self):
        """Two-for-two where each incoming fits one outgoing is not aggregation."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Piece A", 12_000_000, "BOS", "DAL"),
                    player("Piece B", 10_000_000, "BOS", "DAL"),
                    player("Return A", 11_000_000, "DAL", "BOS"),
                    player("Return B", 9_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()

    def test_recently_acquired_player_cannot_be_aggregated(self):
        trade_date = date(2026, 1, 20)
        result = validate_trade(
            build_trade(
                trade_date=trade_date,
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP, roster=13)),
                players=(
                    player(
                        "Just Acquired",
                        10_000_000,
                        "BOS",
                        "DAL",
                        acquired_via_trade_on=trade_date - timedelta(days=30),
                    ),
                    player("Piece B", 12_000_000, "BOS", "DAL"),
                    player("Star", 21_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert not result.legal
        assert Rule.AGGREGATION_WINDOW in error_rules(result)

    def test_the_window_expires(self):
        trade_date = date(2026, 1, 20)
        result = validate_trade(
            build_trade(
                trade_date=trade_date,
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP, roster=13)),
                players=(
                    player(
                        "Acquired Long Ago",
                        10_000_000,
                        "BOS",
                        "DAL",
                        acquired_via_trade_on=trade_date - timedelta(days=61),
                    ),
                    player("Piece B", 12_000_000, "BOS", "DAL"),
                    player("Star", 21_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()

    def test_window_does_not_fire_when_aggregation_is_not_needed(self):
        """Sending two players is only aggregation if the math requires it."""
        trade_date = date(2026, 1, 20)
        result = validate_trade(
            build_trade(
                trade_date=trade_date,
                # DAL needs real cap room to absorb 22,000,000 for 5,000,000.
                teams=(team("BOS", OVER_CAP), team("DAL", 130_000_000, roster=13)),
                players=(
                    player(
                        "Just Acquired",
                        10_000_000,
                        "BOS",
                        "DAL",
                        acquired_via_trade_on=trade_date - timedelta(days=5),
                    ),
                    player("Piece B", 12_000_000, "BOS", "DAL"),
                    player("Modest Return", 5_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()
        assert Rule.AGGREGATION_WINDOW not in rules_fired(result)


class TestPlayerEligibility:
    def test_recently_signed_player_cannot_be_traded(self):
        result = validate_trade(
            build_trade(
                trade_date=date(2025, 10, 15),
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("New Signing", 8_000_000, "BOS", "DAL", signed_on=date(2025, 7, 8)),
                    player("Return", 8_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert not result.legal
        assert Rule.TRADE_RESTRICTION_WINDOW in error_rules(result)

    def test_same_player_after_december_fifteen(self):
        result = validate_trade(
            build_trade(
                trade_date=date(2026, 1, 5),
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("New Signing", 8_000_000, "BOS", "DAL", signed_on=date(2025, 7, 8)),
                    player("Return", 8_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()

    @pytest.mark.parametrize(
        ("signed", "expected"),
        [
            # Summer signing: December 15 binds, not the 90-day clock.
            (date(2025, 7, 8), date(2025, 12, 15)),
            # Late-autumn signing: the 90-day clock runs past December 15.
            (date(2025, 11, 1), date(2026, 1, 30)),
            # In-season signing: December 15 of the *same league year* is past.
            (date(2026, 1, 10), date(2026, 4, 10)),
        ],
    )
    def test_earliest_trade_date(self, signed, expected):
        assert earliest_trade_date(signed) == expected

    def test_no_trade_clause_blocks_without_consent(self):
        base = dict(
            teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
        )
        blocked = validate_trade(
            build_trade(
                **base,
                players=(
                    player("Veteran", 20_000_000, "BOS", "DAL", no_trade_clause=True),
                    player("Return", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert Rule.NO_TRADE_CLAUSE in error_rules(blocked)

        consented = validate_trade(
            build_trade(
                **base,
                players=(
                    player(
                        "Veteran",
                        20_000_000,
                        "BOS",
                        "DAL",
                        no_trade_clause=True,
                        consent_given=True,
                    ),
                    player("Return", 20_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert consented.legal, consented.explain()

    def test_sign_and_trade_cannot_land_on_an_apron_team(self):
        result = validate_trade(
            build_trade(
                teams=(team("GSW", FIRST_APRON), team("DAL", OVER_CAP)),
                players=(
                    player("Outgoing", 20_000_000, "GSW", "DAL"),
                    player("New Guy", 20_000_000, "DAL", "GSW", sign_and_trade=True),
                ),
            )
        )
        assert not result.legal
        assert Rule.SIGN_AND_TRADE_APRON in error_rules(result)


class TestTradeExceptions:
    def _absorb_with_tpe(self, tpe: TradeException):
        return build_trade(
            teams=(
                team("GSW", FIRST_APRON, roster=14, trade_exceptions=(tpe,)),
                team("UTA", UNDER_CAP),
            ),
            players=(player("Absorbed", 6_000_000, "UTA", "GSW"),),
        )

    def test_current_year_exception_is_usable(self):
        result = validate_trade(
            self._absorb_with_tpe(TradeException(6_000_000, "2025-26", "current"))
        )
        assert result.legal, result.explain()

    def test_apron_team_cannot_use_a_prior_year_exception(self):
        result = validate_trade(
            self._absorb_with_tpe(TradeException(6_000_000, "2024-25", "stale"))
        )
        assert not result.legal
        assert Rule.SALARY_MATCH in error_rules(result)
        assert result.per_team["GSW"].match.max_incoming == 0

    def test_second_apron_team_cannot_use_a_sign_and_trade_exception(self):
        trade = build_trade(
            teams=(
                team(
                    "BOS",
                    SECOND_APRON,
                    roster=14,
                    trade_exceptions=(
                        TradeException(6_000_000, "2025-26", "snt", from_sign_and_trade=True),
                    ),
                ),
                team("UTA", UNDER_CAP),
            ),
            players=(player("Absorbed", 6_000_000, "UTA", "BOS"),),
        )
        result = validate_trade(trade)
        assert not result.legal


class TestRosterAndFloor:
    def test_roster_cannot_exceed_fifteen(self):
        result = validate_trade(
            build_trade(
                teams=(team("UTA", UNDER_CAP, roster=15), team("DAL", OVER_CAP, roster=15)),
                players=(
                    player("Sent", 5_000_000, "UTA", "DAL"),
                    player("In A", 4_000_000, "DAL", "UTA"),
                    player("In B", 4_000_000, "DAL", "UTA"),
                    player("In C", 4_000_000, "DAL", "UTA"),
                ),
            )
        )
        assert not result.legal
        assert Rule.ROSTER_LIMIT in error_rules(result)
        assert result.per_team["UTA"].roster_after == 17

    def test_dropping_below_fourteen_is_a_warning_not_an_error(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP, roster=14), team("DAL", OVER_CAP, roster=13)),
                players=(
                    player("Piece A", 10_000_000, "BOS", "DAL"),
                    player("Piece B", 12_000_000, "BOS", "DAL"),
                    player("Star", 21_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal, result.explain()
        assert Rule.ROSTER_MINIMUM in {f.rule for f in result.warnings()}

    def test_falling_below_the_salary_floor_is_a_warning(self):
        result = validate_trade(
            build_trade(
                teams=(team("UTA", 140_000_000), team("DAL", 140_000_000, roster=14)),
                players=(player("Sent", 5_000_000, "UTA", "DAL"),),
            )
        )
        assert result.legal, result.explain()
        assert Rule.MIN_TEAM_SALARY in {f.rule for f in result.warnings()}

    def test_hard_cap_warning_when_taking_back_over_110_percent(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 25_000_000, "DAL", "BOS"),
                ),
            )
        )
        assert result.legal
        hard_capped = [f for f in result.warnings() if f.rule == Rule.HARD_CAP]
        assert [f.team for f in hard_capped] == ["BOS"]

    def test_cap_room_absorption_does_not_hard_cap(self):
        """A team using room, not the exception, is not hard-capped."""
        result = validate_trade(
            build_trade(
                teams=(team("UTA", UNDER_CAP, roster=14), team("DAL", OVER_CAP)),
                players=(player("Absorbed", 9_000_000, "DAL", "UTA"),),
            )
        )
        assert Rule.HARD_CAP not in rules_fired(result)


class TestCash:
    def test_second_apron_team_cannot_send_cash(self):
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
        assert not result.legal
        assert Rule.CASH_SECOND_APRON in error_rules(result)

    def test_annual_cash_limit(self):
        result = validate_trade(
            build_trade(
                teams=(
                    team("BOS", OVER_CAP, cash_sent_this_year=8_000_000),
                    team("DAL", OVER_CAP),
                ),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                cash=(CashAsset("BOS", "DAL", 1_000_000),),
            )
        )
        assert not result.legal  # 9,000,000 > the 8,527,000 limit
        assert Rule.CASH_LIMIT in error_rules(result)


class TestDraftPicks:
    def test_stepien_rule_blocks_consecutive_bare_years(self):
        result = validate_trade(
            build_trade(
                teams=(
                    team(
                        "BOS",
                        OVER_CAP,
                        first_round_picks=((2026, 1), (2027, 1), (2028, 1)),
                    ),
                    team("DAL", OVER_CAP, first_round_picks=((2026, 1), (2027, 1))),
                ),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                picks=(
                    PickAsset("BOS", "DAL", 2027, 1),
                    PickAsset("BOS", "DAL", 2028, 1),
                ),
            )
        )
        assert not result.legal
        assert Rule.STEPIEN in error_rules(result)

    def test_alternating_years_are_fine(self):
        result = validate_trade(
            build_trade(
                teams=(
                    team(
                        "BOS",
                        OVER_CAP,
                        first_round_picks=(
                            (2026, 1),
                            (2027, 1),
                            (2028, 1),
                            (2029, 1),
                            (2030, 1),
                        ),
                    ),
                    team("DAL", OVER_CAP, first_round_picks=((2026, 1),)),
                ),
                players=(
                    player("Out East", 20_000_000, "BOS", "DAL"),
                    player("Out West", 20_000_000, "DAL", "BOS"),
                ),
                picks=(
                    PickAsset("BOS", "DAL", 2027, 1),
                    PickAsset("BOS", "DAL", 2029, 1),
                ),
            )
        )
        assert result.legal, result.explain()


class TestStructure:
    def test_one_team_is_not_a_trade(self):
        result = validate_trade(build_trade(teams=(team("BOS", OVER_CAP),)))
        assert not result.legal
        assert Rule.STRUCTURE in error_rules(result)

    def test_asset_moving_to_a_non_participant(self):
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
                players=(player("Ghost", 10_000_000, "BOS", "PHX"),),
            )
        )
        assert not result.legal
        assert Rule.STRUCTURE in error_rules(result)

    def test_trade_with_no_assets(self):
        result = validate_trade(
            build_trade(teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)))
        )
        assert not result.legal

    def test_malformed_trade_does_not_crash_and_reports_no_teams(self):
        """Agents emit garbage; the validator must reject it, not raise."""
        result = validate_trade(
            build_trade(
                teams=(team("BOS", OVER_CAP), team("BOS", OVER_CAP)),
                players=(player("Self Deal", 10_000_000, "BOS", "BOS"),),
            )
        )
        assert not result.legal
        assert result.per_team == {}


class TestThreeTeamTrades:
    def test_each_team_is_matched_independently(self):
        """BOS -> DAL -> UTA -> BOS, with DAL over its bracket."""
        result = validate_trade(
            build_trade(
                teams=(
                    team("BOS", OVER_CAP),
                    team("DAL", OVER_CAP),
                    team("UTA", UNDER_CAP),
                ),
                players=(
                    player("A", 20_000_000, "BOS", "DAL"),
                    player("B", 8_000_000, "DAL", "UTA"),
                    player("C", 18_000_000, "UTA", "BOS"),
                ),
            )
        )
        # DAL sends 8,000,000 (bracket limit 16,500,000) and takes back
        # 20,000,000. Over by 3,500,000.
        assert not result.legal
        offenders = {f.team for f in result.errors() if f.rule == Rule.SALARY_MATCH}
        assert offenders == {"DAL"}


def test_summarize_is_readable():
    result = validate_trade(
        build_trade(
            teams=(team("BOS", OVER_CAP), team("DAL", OVER_CAP)),
            players=(
                player("Out East", 20_000_000, "BOS", "DAL"),
                player("Out West", 20_000_000, "DAL", "BOS"),
            ),
        )
    )
    text = summarize(result)
    assert "APPROVED" in text
    assert "BOS" in text and "DAL" in text


def test_findings_carry_severity_and_stable_rule_ids():
    result = validate_trade(
        build_trade(
            teams=(team("BOS", SECOND_APRON), team("DAL", OVER_CAP)),
            players=(
                player("Out East", 20_000_000, "BOS", "DAL"),
                player("Out West", 25_000_000, "DAL", "BOS"),
            ),
        )
    )
    assert all(isinstance(f.severity, Severity) for f in result.findings)
    assert all(f.rule.isupper() for f in result.findings)
