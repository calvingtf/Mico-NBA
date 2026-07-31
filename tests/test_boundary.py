"""The LLM -> rules boundary.

Two things are being defended here, and they are the reason M1 exists.

**Only rules/ may approve.** Not "agents are written not to decide legality" —
there is no path by which they could. The proposal schema has no field for it,
assembly takes every figure from the snapshot, and the only call to
``validate_trade`` outside ``rules/`` is in ``boundary.judge``.

**All three verdicts are reachable.** Approved, rejected, and undetermined get
exercised with real cap arithmetic and no model in the loop, so the wiring is
proven independently of whether a given model happens to propose something
legal on a given day.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mironba.agents.gm import GMContext, GMPersona, RosterEntry
from mironba.llm.schemas import TradeProposal
from mironba.rules.cap import ApronTier
from mironba.rules.constants import environment_for
from mironba.rules.trade_validator import ReSignStatus, Verdict, VerdictUndetermined
from mironba.sim.boundary import (
    BYCResolution,
    MalformedProposal,
    assemble,
    judge,
    rejection_reason,
)

SEASON = "2024-25"
ENV = environment_for(SEASON)
TRADE_DATE = date(2025, 2, 6)

RESOLVED = BYCResolution(mode="assume_not_re_signed", sourced=False)
UNRESOLVED = BYCResolution(mode="unresolved")


def context(
    *,
    own: list[tuple[str, str, int]],
    theirs: list[tuple[str, str, int]],
    team_salary: int,
    roster_count: int = 14,
) -> GMContext:
    return GMContext(
        team_id="LAL",
        season=SEASON,
        scenario_seed="test",
        own_roster=tuple(RosterEntry(*p) for p in own),
        partner_team="GSW",
        partner_roster=tuple(RosterEntry(*p) for p in theirs),
        team_salary=team_salary,
        tier=ApronTier.OVER_CAP,
        roster_count=roster_count,
    )


def proposal(send: list[str], receive: list[str], partner: str = "GSW") -> TradeProposal:
    return TradeProposal(
        partner_team=partner,
        send_player_ids=send,
        receive_player_ids=receive,
        reason="test proposal",
    )


PERSONA = GMPersona(label="t", risk_tolerance=0.5, win_now_horizon=2, asset_hoarding=0.2)


def build(prop, ctx, *, byc=RESOLVED, persona=PERSONA, partner_salary=150_000_000):
    return assemble(
        prop,
        ctx,
        persona,
        trade_date=TRADE_DATE,
        partner_salary=partner_salary,
        partner_roster_count=14,
        byc=byc,
    )


class TestSalariesComeFromTheSnapshot:
    def test_the_model_never_supplies_a_figure(self):
        """A proposal is ids. Every dollar is looked up here."""
        ctx = context(
            own=[("p1", "Player One", 30_000_000)],
            theirs=[("p2", "Player Two", 31_000_000)],
            team_salary=170_000_000,
        )
        trade = build(proposal(["p1"], ["p2"]), ctx)
        assert {p.player_id: p.salary for p in trade.players} == {
            "p1": 30_000_000,
            "p2": 31_000_000,
        }

    def test_a_proposal_has_no_field_that_could_carry_a_salary(self):
        assert not any(
            "salary" in name and name.endswith(("salary", "salaries"))
            for name in TradeProposal.model_fields
        )


class TestMalformedProposals:
    """Things real models do. Counted separately from rules rejections."""

    def setup_method(self):
        self.ctx = context(
            own=[("p1", "One", 10_000_000), ("p2", "Two", 9_000_000)],
            theirs=[("q1", "Three", 10_500_000)],
            team_salary=150_000_000,
        )

    def test_an_invented_player_id_is_malformed(self):
        with pytest.raises(MalformedProposal, match="not on LAL's roster"):
            build(proposal(["ghost"], ["q1"]), self.ctx)

    def test_sending_a_player_from_the_wrong_side_says_so(self):
        with pytest.raises(MalformedProposal, match="it is on GSW's"):
            build(proposal(["q1"], ["q1"]), self.ctx)

    def test_receiving_your_own_player_says_so(self):
        with pytest.raises(MalformedProposal, match="it is on LAL's"):
            build(proposal(["p1"], ["p1"]), self.ctx)

    def test_the_wrong_partner_is_malformed(self):
        with pytest.raises(MalformedProposal, match="not the counterparty"):
            build(proposal(["p1"], ["q1"], partner="BOS"), self.ctx)

    def test_a_duplicated_player_is_malformed(self):
        with pytest.raises(MalformedProposal, match="sent twice"):
            build(proposal(["p1", "p1"], ["q1"]), self.ctx)

    def test_every_problem_is_reported_at_once(self):
        """One round trip per revision. Drip-feeding errors wastes the retry."""
        with pytest.raises(MalformedProposal) as exc:
            build(proposal(["ghost"], ["alsoghost"], partner="BOS"), self.ctx)
        assert len(exc.value.reasons) == 3

    def test_persona_asset_hoarding_is_enforced_not_merely_suggested(self):
        """The persona parameter feeds code, not only the prompt."""
        hoarder = GMPersona(label="h", asset_hoarding=0.9)
        assert hoarder.max_assets_out == 1
        with pytest.raises(MalformedProposal, match="asset_hoarding"):
            build(proposal(["p1", "p2"], ["q1"]), self.ctx, persona=hoarder)


class TestAllThreeVerdictsAreReachable:
    def test_approved(self):
        """Under the cap, comfortably matched."""
        ctx = context(
            own=[("p1", "One", 20_000_000)],
            theirs=[("q1", "Two", 20_500_000)],
            team_salary=150_000_000,
        )
        result = judge(build(proposal(["p1"], ["q1"]), ctx))
        assert result.verdict is Verdict.APPROVED
        assert result.legal is True

    def test_rejected_with_a_usable_reason(self):
        """Taking back far more than the brackets allow."""
        ctx = context(
            own=[("p1", "One", 5_000_000)],
            theirs=[("q1", "Two", 40_000_000)],
            team_salary=180_000_000,
        )
        result = judge(build(proposal(["p1"], ["q1"]), ctx))
        assert result.verdict is Verdict.REJECTED
        reason = rejection_reason(result)
        assert "SALARY_MATCH" in reason
        assert "$" in reason  # the numbers, so a revision has something to aim at

    def test_undetermined_when_byc_is_unresolved(self):
        """The path a snapshot-derived trade actually takes.

        re_sign_status is UNKNOWN for every ingested player, so this is not an
        exotic case — it is the default, and the validator refuses to guess.
        """
        ctx = context(
            own=[("p1", "One", 20_000_000)],
            theirs=[("q1", "Two", 20_500_000)],
            team_salary=150_000_000,
        )
        trade = build(proposal(["p1"], ["q1"]), ctx, byc=UNRESOLVED)
        assert all(p.re_sign_status is ReSignStatus.UNKNOWN for p in trade.players)
        result = judge(trade)
        assert result.verdict is Verdict.UNDETERMINED
        with pytest.raises(VerdictUndetermined):
            _ = result.legal

    def test_undetermined_gives_the_agent_something_to_read(self):
        ctx = context(
            own=[("p1", "One", 20_000_000)],
            theirs=[("q1", "Two", 20_500_000)],
            team_salary=150_000_000,
        )
        result = judge(build(proposal(["p1"], ["q1"]), ctx, byc=UNRESOLVED))
        assert "UNDETERMINED" in rejection_reason(result)


class TestBYCResolution:
    def test_an_unsourced_assumption_is_flagged_as_one(self):
        assert RESOLVED.is_assumption is True
        assert BYCResolution("assume_not_re_signed", sourced=True).is_assumption is False
        assert UNRESOLVED.is_assumption is False

    def test_unresolved_means_unknown_not_a_convenient_default(self):
        assert UNRESOLVED.status is ReSignStatus.UNKNOWN


class TestOnlyRulesMayApprove:
    def test_no_agent_or_llm_module_imports_the_validator(self):
        """The charter's first non-negotiable, checked structurally.

        `boundary.py` is the single crossing point. If an agent ever imports
        validate_trade directly, the LLM has a path to deciding legality and
        this fails.
        """
        root = Path(__file__).resolve().parents[1] / "mironba"
        allowed = {"boundary.py", "tick.py", "bench.py"}
        offenders = []
        for part in ("agents", "llm"):
            for path in (root / part).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "validate_trade" in text and path.name not in allowed:
                    offenders.append(str(path.relative_to(root)))
        assert not offenders, (
            f"modules reaching past the boundary: {offenders}. Only "
            "sim/boundary.py may call the validator."
        )

    def test_the_verdict_is_never_taken_from_the_model(self):
        """Nothing in the agent-facing schemas can express approval."""
        blob = str(TradeProposal.model_json_schema()).lower()
        for token in ("approved", "legal", "verdict", "valid"):
            assert token not in blob
