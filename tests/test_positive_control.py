"""The positive control must actually contain a good legal trade.

Without it, a stand-pat rate is uninterpretable: declining a bad trade is
correct and most available trades are bad, so a model that always refuses and a
model with excellent judgment score identically. Haiku went 0/24 across Los
Angeles and Chicago and the result could not be read either way.

This scenario is the discriminator, so its premise is verified here rather than
asserted in a YAML comment. A comment can rot.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from mironba.rules.cap import ApronTier, tier_for_salary
from mironba.rules.constants import environment_for
from mironba.rules.solver import Asset, TradeIntent, solve
from mironba.rules.trade_validator import TeamTradeState

SNAP = Path(__file__).resolve().parents[1] / "mironba" / "data" / "snapshots" / "bbref-2024-25"
BUYER, SELLER, TARGET = "DET", "BKN", "russeda01"
MIN_PACKAGES = 3


@pytest.fixture(scope="module")
def book():
    if not (SNAP / "contracts.csv").is_file():
        pytest.skip("2024-25 snapshot not ingested")
    with (SNAP / "contracts.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    with (SNAP / "players.csv").open(encoding="utf-8") as handle:
        names = {r["player_id"]: r["name"] for r in csv.DictReader(handle)}
    payroll, roster = {}, {}
    for r in rows:
        payroll[r["team_id"]] = payroll.get(r["team_id"], 0) + int(r["salary"])
        roster.setdefault(r["team_id"], []).append(r)
    return payroll, roster, names


def _assets(roster, names, team):
    return {
        r["player_id"]: Asset(r["player_id"], names.get(r["player_id"], r["player_id"]),
                              int(r["salary"]))
        for r in roster[team]
    }


class TestTheControlIsActuallyControlled:
    def test_the_buyer_is_clear_of_every_apron(self, book):
        """No cap-mechanics reason to decline."""
        payroll, _, _ = book
        env = environment_for("2024-25")
        assert tier_for_salary(payroll[BUYER], env) is ApronTier.UNDER_CAP
        assert payroll[BUYER] < env.first_apron
        assert env.first_apron - payroll[BUYER] > 30_000_000

    def test_a_legal_package_exists_and_there_are_several(self, book):
        """The premise. If this fails the scenario proves nothing."""
        payroll, roster, names = book
        own, theirs = _assets(roster, names, BUYER), _assets(roster, names, SELLER)
        assert TARGET in theirs, "target not on the seller's roster in this snapshot"
        result = solve(
            TradeIntent(target_player_ids=(TARGET,), tradeable_asset_ids=tuple(own)),
            own=own, theirs=theirs,
            own_team=TeamTradeState(BUYER, payroll[BUYER], 14),
            partner_team=TeamTradeState(SELLER, payroll[SELLER], 14),
            season="2024-25", trade_date=date(2025, 2, 6),
        )
        assert result.satisfiable
        assert len(result.packages) >= MIN_PACKAGES, (
            f"only {len(result.packages)} legal packages; the control needs "
            "several so a decline cannot be blamed on a thin option set"
        )

    def test_the_target_is_well_above_the_median_player(self, book):
        """"Good" is measured by the project's own value model, not asserted."""
        from mironba.sim.deadline import FRINGE_VALUE, player_values

        values = player_values("2024-25")
        if not values:
            pytest.skip("value model inputs not present")
        assert values.get(TARGET, 0) > 2 * FRINGE_VALUE

    def test_the_scenario_file_matches_what_is_tested_here(self):
        text = (Path(__file__).resolve().parents[1] / "configs" / "scenario"
                / "positive-control-pistons.yaml").read_text(encoding="utf-8")
        assert "team: DET" in text
        assert "partner: BKN" in text
        assert "positive control" in text.lower()
