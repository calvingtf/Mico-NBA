"""Candidate derivation from ingested data.

The clause parser is the fragile part — Basketball-Reference writes trades
three different ways — so the shapes that previously failed are pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mironba.data import candidates
from mironba.rules.cap import ApronTier
from mironba.rules.constants import environment_for

SNAPSHOTS = (
    Path(__file__).resolve().parents[1] / "mironba" / "data" / "snapshots"
)

NAMES = {
    "holidjr01": "Jrue Holiday",
    "brogdma01": "Malcolm Brogdon",
    "griffaj01": "A.J. Griffin",
    "larsspe01": "Pelle Larsson",
    "djurini01": "Nikola Djurisic",
}
SALARY = {
    "holidjr01": 36_861_707,
    "brogdma01": 22_500_000,
    "griffaj01": 3_712_920,
    "larsspe01": 1_157_153,
    "djurini01": 1_157_153,
}

TWO_TEAM = (
    "The Boston Celtics traded Jrue Holiday{{holidjr01}} to the "
    "Portland Trail Blazers for Malcolm Brogdon{{brogdma01}}."
)

THREE_TEAM = (
    "In a 3-team trade, the Atlanta Hawks traded A.J. Griffin{{griffaj01}} to the "
    "Houston Rockets ; the Atlanta Hawks traded cash to the Miami Heat ; the "
    "Houston Rockets traded Pelle Larsson{{larsspe01}} to the Miami Heat ; and the "
    "Miami Heat traded Nikola Djurisic{{djurini01}} to the Atlanta Hawks ."
)

PICKS_ONLY = (
    "The Detroit Pistons traded a 2027 2nd round draft pick to the "
    "Washington Wizards ."
)


class TestClauseParsing:
    def test_plain_two_team_sentence(self):
        legs = candidates.parse_trade(TWO_TEAM, NAMES, SALARY)
        assert [(leg.player_id, leg.from_team, leg.to_team) for leg in legs] == [
            ("holidjr01", "BOS", "POR"),
            ("brogdma01", "POR", "BOS"),
        ]

    def test_multi_team_semicolon_sentence(self):
        """This shape returned nothing until the trailing connective was stripped."""
        legs = candidates.parse_trade(THREE_TEAM, NAMES, SALARY)
        moves = {(leg.player_id, leg.from_team, leg.to_team) for leg in legs}
        assert ("griffaj01", "ATL", "HOU") in moves
        assert ("larsspe01", "HOU", "MIA") in moves
        assert ("djurini01", "MIA", "ATL") in moves

    def test_pick_only_trade_yields_no_legs(self):
        """Correct silence, not a parse failure — there is no player to price."""
        assert candidates.parse_trade(PICKS_ONLY, NAMES, SALARY) == []

    def test_unknown_player_id_still_produces_a_leg_without_a_price(self):
        text = "The Boston Celtics traded Someone{{nobodyxx01}} to the Utah Jazz ."
        legs = candidates.parse_trade(text, NAMES, SALARY)
        assert len(legs) == 1
        assert legs[0].salary is None


class TestTierExclusion:
    def test_team_near_a_boundary_is_excluded(self):
        """Our team salary is an approximation; near a line it decides the tier."""
        env = environment_for("2024-25")
        near = env.first_apron - 1_000_000
        tier, margin = candidates._tier_confidence("XXX", {"XXX": near}, env)
        assert tier is None
        assert margin == 1_000_000

    def test_team_comfortably_inside_a_tier_is_kept(self):
        env = environment_for("2024-25")
        clear = env.second_apron + 10_000_000
        tier, margin = candidates._tier_confidence("XXX", {"XXX": clear}, env)
        assert tier is ApronTier.SECOND_APRON
        assert margin >= candidates.TIER_BOUNDARY_EXCLUSION

    def test_exclusion_threshold_is_three_million(self):
        assert candidates.TIER_BOUNDARY_EXCLUSION == 3_000_000


class TestAgainstRealSnapshots:
    @pytest.mark.parametrize(
        "snapshot", ["bbref-2023-24", "bbref-2024-25", "bbref-2025-26"]
    )
    def test_every_player_bearing_trade_parses(self, snapshot):
        """A silent drop here would understate coverage and hide viable rows."""
        if not (SNAPSHOTS / snapshot / "transactions.csv").exists():
            pytest.skip(
                "ingested tables absent (not redistributed); rebuild with "
                f"`python -m mironba.data.ingest.build --seasons {snapshot[6:]}`"
            )
        salary, names, transactions, _ = candidates.load_snapshot(snapshot)
        bearing = [
            row
            for row in transactions
            if row["is_trade"] == "1" and "{{" in row["marked_text"]
        ]
        assert bearing, snapshot
        unparsed = [
            row["text"][:80]
            for row in bearing
            if not candidates.parse_trade(row["marked_text"], names, salary)
        ]
        assert not unparsed, f"{snapshot}: {len(unparsed)} unparsed, e.g. {unparsed[:2]}"

    def test_candidates_never_auto_promote(self):
        """The module proposes; a human confirms.

        Guards the review step: if this module ever gains the ability to write
        the fixture file, candidates would silently become verified evidence
        and the coverage matrix would stop meaning anything.
        """
        source = Path(candidates.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        body = code.split('"""', 2)[-1]  # drop the module docstring
        assert "real_trades.yaml" not in body
        assert "yaml" not in body.lower().replace("yaml.safe", "")
