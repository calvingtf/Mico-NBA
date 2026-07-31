"""Three-team trades: parsing, validation, and what they do to the denominator.

The validator has been N-team since M0 — ``Trade.teams`` is a tuple and each
participant is checked against its own cap position. What was two-team-only was
the *parser* and the eval harness, and that is what capped the scoreable
denominator at 4 across three deadlines while 21 three-team trades sat
unparsed in the same log.
"""

from __future__ import annotations

from datetime import date

import pytest

from mironba.eval.real_trades import (
    Move,
    PickMove,
    RealTrade,
    check,
    parse_trades,
    parse_two_team_trades,
)
from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    TeamTradeState,
    Trade,
    Verdict,
    validate_trade,
)

SEASONS = ("2023-24", "2024-25", "2025-26")


def _trade(salaries, moves, season="2024-25"):
    """A trade over N teams, each given a payroll that cannot itself reject."""
    teams = sorted({t for m in moves for t in (m[1], m[2])})
    return Trade(
        season=season,
        trade_date=date(2025, 2, 1),
        teams=tuple(TeamTradeState(t, 150_000_000, 12) for t in teams),
        players=tuple(
            PlayerAsset(
                player_id=pid, name=pid, salary=salaries[pid],
                from_team=src, to_team=dst,
                re_sign_status=ReSignStatus.UNKNOWN,
            )
            for pid, src, dst in moves
        ),
    )


class TestValidatorIsAlreadyNTeam:
    def test_each_team_is_matched_against_its_own_position(self):
        """The middle team of a cycle is checked on its own books.

        A sends to B, B sends to C, C sends to A. Nobody trades with the team
        they receive from, which is the shape a two-team validator cannot
        express at all.
        """
        salaries = {"p1": 10_000_000, "p2": 10_500_000, "p3": 9_800_000}
        result = validate_trade(
            _trade(salaries, [("p1", "ATL", "HOU"), ("p2", "HOU", "MIA"),
                              ("p3", "MIA", "ATL")]),
            environment_for("2024-25"),
        )
        assert result.verdict is not Verdict.REJECTED
        assert set(result.per_team) == {"ATL", "HOU", "MIA"}

    def test_one_participant_failing_rejects_the_whole_trade(self):
        """Legality is per-team and conjunctive. A three-team deal in which one
        leg does not match is not two-thirds legal."""
        salaries = {"big": 40_000_000, "small": 1_000_000, "mid": 5_000_000}
        result = validate_trade(
            _trade(salaries, [("big", "ATL", "HOU"), ("small", "HOU", "MIA"),
                              ("mid", "MIA", "ATL")]),
            environment_for("2024-25"),
        )
        assert result.verdict is Verdict.REJECTED

    def test_a_pure_absorber_is_a_participant_not_a_bystander(self):
        """The classic third team: takes salary, sends no player."""
        salaries = {"p1": 20_000_000, "p2": 19_000_000}
        trade = _trade(salaries, [("p1", "ATL", "MIA"), ("p2", "HOU", "ATL")])
        result = validate_trade(trade, environment_for("2024-25"))
        assert "MIA" in result.per_team


class TestParser:
    @pytest.mark.parametrize("season", SEASONS)
    def test_three_team_trades_are_found(self, season):
        trades = parse_trades(season)
        if not trades:
            pytest.skip(f"no transaction snapshot for {season}")
        assert any(t.n_teams == 3 for t in trades)

    @pytest.mark.parametrize("season", SEASONS)
    def test_max_teams_is_respected(self, season):
        for trade in parse_trades(season, max_teams=3):
            assert 2 <= trade.n_teams <= 3

    @pytest.mark.parametrize("season", SEASONS)
    def test_two_team_parsing_is_unchanged(self, season):
        """The old figures must stay recomputable, or the comparison is lost."""
        old = parse_two_team_trades(season)
        assert all(t.n_teams == 2 for t in old)
        new_two = [t for t in parse_trades(season) if t.n_teams == 2]
        assert {(t.when, t.teams) for t in old} == {(t.when, t.teams) for t in new_two}

    @pytest.mark.parametrize("season", SEASONS)
    def test_every_move_stays_inside_the_participant_set(self, season):
        for trade in parse_trades(season):
            for move in trade.moves:
                assert move.from_team in trade.teams
                assert move.to_team in trade.teams
                assert move.from_team != move.to_team

    @pytest.mark.parametrize("season", SEASONS)
    def test_picks_are_parsed_with_a_direction(self, season):
        picks = [p for t in parse_trades(season) for p in t.picks]
        if not picks:
            pytest.skip("no picks in this snapshot")
        for pick in picks:
            assert pick.round in (1, 2)
            assert 2000 < pick.draft_year < 2040
            assert pick.from_team != pick.to_team

    def test_the_later_selected_player_is_not_treated_as_traded(self):
        """"a 2025 2nd round pick (X was later selected)" moved a pick, not X.

        He had no NBA contract at the time and pricing him would be pricing a
        player who did not exist in that season's cap.
        """
        trades = parse_trades("2024-25")
        if not trades:
            pytest.skip("no snapshot")
        for trade in trades:
            for move in trade.moves:
                assert move.player_id


class TestScoreableDenominator:
    def test_multi_team_support_raises_the_scoreable_count(self):
        """The whole point of the change, asserted as a number.

        Two-team parsing yielded 5 scoreable trades across three seasons. If
        this drops back to that, multi-team support has silently regressed.
        """
        def scoreable(parser):
            return sum(
                1
                for season in SEASONS
                for trade in parser(season)
                if trade.representable and check(trade).scored
            )

        two_team = scoreable(parse_two_team_trades)
        all_teams = scoreable(parse_trades)
        if all_teams == 0:
            pytest.skip("no snapshots ingested")
        assert all_teams > two_team

    def test_representable_requires_every_participant_to_send_a_player(self):
        trade = RealTrade(
            when=date(2025, 2, 1), season="2024-25",
            teams=("ATL", "HOU", "MIA"),
            moves=(Move("p1", "ATL", "HOU"), Move("p2", "HOU", "MIA")),
            text="",
        )
        assert not trade.representable
        assert trade.sends_only_picks == ("MIA",)

    def test_sends_and_receives_are_per_team(self):
        trade = RealTrade(
            when=date(2025, 2, 1), season="2024-25",
            teams=("ATL", "HOU", "MIA"),
            moves=(Move("p1", "ATL", "HOU"), Move("p2", "HOU", "MIA"),
                   Move("p3", "MIA", "ATL")),
            text="",
            picks=(PickMove("ATL", "MIA", 2031, 1),),
        )
        assert trade.representable
        assert trade.sends("ATL") == ("p1",)
        assert trade.receives("ATL") == ("p3",)
        assert trade.n_teams == 3


class TestRecallCountsTradesNotProposals:
    def test_recall_numerator_is_distinct_actual_trades(self):
        """Several proposals can hit one real trade.

        Dividing matching *proposals* by real trades made recall read 200%,
        which is the shape of a metric bug rather than a good result.
        """
        from mironba.sim.deadline import DeadlineScore

        score = DeadlineScore(
            proposed=100, actual=3, representable=3,
            pair_hits=9, actual_matched=2, player_hits=0, exact_hits=0,
            solver_legal=3, solver_scored=3,
        )
        assert score.recall <= 1.0
        assert score.recall == pytest.approx(2 / 3)
        assert score.precision == pytest.approx(0.09)
