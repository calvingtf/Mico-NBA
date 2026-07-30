from __future__ import annotations

from datetime import date

import pytest
from coverage_matrix import summary

from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import PlayerAsset, TeamTradeState, Trade


def pytest_report_header():
    """Put the coverage state at the top of every run.

    The deferred REALITY cells no longer fail the suite, which means nothing
    would otherwise mention them. A number that only appears when someone goes
    looking is a number that quietly grows.
    """
    return summary()


@pytest.fixture
def env_2526():
    return environment_for("2025-26")


@pytest.fixture
def env_2324():
    return environment_for("2023-24")


def player(
    name: str,
    salary: int,
    from_team: str,
    to_team: str,
    **kwargs,
) -> PlayerAsset:
    return PlayerAsset(
        player_id=name.lower().replace(" ", "-"),
        name=name,
        salary=salary,
        from_team=from_team,
        to_team=to_team,
        **kwargs,
    )


def team(team_id: str, salary: int, roster: int = 15, **kwargs) -> TeamTradeState:
    return TeamTradeState(
        team_id=team_id, team_salary=salary, roster_count=roster, **kwargs
    )


def build_trade(
    *,
    season: str = "2025-26",
    trade_date: date = date(2026, 1, 20),
    teams: tuple[TeamTradeState, ...],
    players: tuple[PlayerAsset, ...] = (),
    picks: tuple = (),
    cash: tuple = (),
) -> Trade:
    return Trade(
        season=season,
        trade_date=trade_date,
        teams=teams,
        players=players,
        picks=picks,
        cash=cash,
    )


def rules_fired(validation) -> set[str]:
    return {f.rule for f in validation.findings}


def error_rules(validation) -> set[str]:
    return {f.rule for f in validation.errors()}
