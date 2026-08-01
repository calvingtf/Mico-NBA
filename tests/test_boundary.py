"""The LLM -> rules boundary, under the M1.5 architecture.

Two invariants carry over from M1 unchanged, and they are the reason this file
exists:

**Only rules/ may approve.** Not "agents are written not to decide legality" —
there is no path by which they could.

**Salaries never come from a model.** Every figure in a Trade is looked up from
the snapshot.

M1.5 adds a third, stronger than either: **a package is unrepresentable in any
agent-facing schema.** Under M1 the model emitted packages and the validator
rejected them, measured at 0 legal in 12. Now the model states an intent, the
solver builds every package that exists, and the model picks an index. There is
no schema in which an illegal package could be written down.
"""

from __future__ import annotations

import re

import json
from datetime import date
from pathlib import Path

import pytest

from mironba.llm.schemas import AGENT_SCHEMAS, FORBIDDEN_FIELD_TOKENS
from mironba.rules.constants import environment_for
from mironba.rules.solver import Asset, TradeIntent, build_trade, solve
from mironba.rules.trade_validator import (
    ReSignStatus,
    TeamTradeState,
    Verdict,
    VerdictUndetermined,
)
from mironba.sim.boundary import BYCResolution, judge, rejection_reason

SEASON = "2024-25"
ENV = environment_for(SEASON)
TRADE_DATE = date(2025, 2, 6)

RESOLVED = BYCResolution(mode="assume_not_re_signed", sourced=False)
UNRESOLVED = BYCResolution(mode="unresolved")


def assets(*rows):
    return {pid: Asset(pid, pid.upper(), salary) for pid, salary in rows}


def team(team_id, salary, roster=14):
    return TeamTradeState(team_id=team_id, team_salary=salary, roster_count=roster)


def solve_for(own, theirs, *, own_salary=150_000_000, byc=RESOLVED, **kwargs):
    return solve(
        TradeIntent(tuple(theirs), tuple(own)),
        own=own,
        theirs=theirs,
        own_team=team("LAL", own_salary),
        partner_team=team("GSW", 150_000_000),
        season=SEASON,
        trade_date=TRADE_DATE,
        re_sign_status=byc.status,
        **kwargs,
    )


class TestSalariesComeFromTheSnapshot:
    def test_the_model_never_supplies_a_figure(self):
        own = assets(("p1", 30_000_000))
        theirs = assets(("p2", 31_000_000))
        trade = build_trade(
            ("p1",),
            ("p2",),
            own=own,
            theirs=theirs,
            own_team=team("LAL", 170_000_000),
            partner_team=team("GSW", 150_000_000),
            season=SEASON,
            trade_date=TRADE_DATE,
        )
        assert {p.player_id: p.salary for p in trade.players} == {
            "p1": 30_000_000,
            "p2": 31_000_000,
        }

    def test_no_agent_facing_schema_can_state_a_salary(self):
        for schema in AGENT_SCHEMAS:
            blob = json.dumps(schema.model_json_schema()).lower()
            for token in FORBIDDEN_FIELD_TOKENS:
                assert f'"{token}"' not in blob, (
                    f"{schema.__name__} exposes {token!r} to the model"
                )


class TestPackagesAreUnrepresentable:
    """The M1.5 invariant."""

    def test_no_agent_facing_schema_can_express_a_package(self):
        """A package pairs outgoing players with incoming ones.

        If a model can write that pairing down, it can write down an illegal
        one, and we are back to the design that measured 0 legal proposals in
        12 attempts. An intent names wants; only the solver pairs them.
        """
        package_tokens = ("send", "receive", "outgoing", "incoming", "package", "offer")
        for schema in AGENT_SCHEMAS:
            fields = {name.lower() for name in schema.model_fields}
            for token in package_tokens:
                assert not any(token in name for name in fields), (
                    f"{schema.__name__} has a {token!r} field — that is a package"
                )

    def test_the_intent_schema_names_wants_not_pairings(self):
        from mironba.llm.schemas import TradeIntent as IntentForm

        assert set(IntentForm.model_fields) == {
            "target_player_ids",
            "tradeable_asset_ids",
            "excluded_player_ids",
            "priority",
            "reason",
        }

    def test_selection_is_an_index_not_a_structure(self):
        from mironba.llm.schemas import PackageSelection

        spec = PackageSelection.model_json_schema()["properties"]["selection"]
        assert spec["type"] == "integer"

    def test_a_declining_index_is_available(self):
        """Declining must be expressible, or the model will invent a way."""
        from mironba.llm.schemas import PackageSelection

        assert PackageSelection(selection=-1, reason="none are worth it").declined


class TestAllThreeVerdictsAreReachable:
    def test_approved(self):
        own = assets(("p1", 20_000_000))
        theirs = assets(("q1", 20_500_000))
        result = solve_for(own, theirs)
        assert result.satisfiable
        assert result.packages[0].verdict is Verdict.APPROVED

    def test_rejected_is_now_unreachable_through_the_agent_path(self):
        """Rejections still exist; the agent simply cannot cause one.

        A package the validator would reject is never returned by the solver,
        so no selection the model makes can produce one. The rejection path
        remains exercised directly against validate_trade in the M0 suite.
        """
        own = assets(("p1", 5_000_000))
        theirs = assets(("q1", 40_000_000))
        result = solve_for(own, theirs, own_salary=180_000_000)
        assert not result.satisfiable
        assert result.binding_constraint == "SALARY_MATCH"

    def test_undetermined_when_byc_is_unresolved(self):
        own = assets(("p1", 20_000_000))
        theirs = assets(("q1", 20_500_000))
        result = solve_for(own, theirs, byc=UNRESOLVED)
        assert result.satisfiable
        package = result.packages[0]
        assert package.verdict is Verdict.UNDETERMINED

    def test_an_undetermined_package_still_raises_on_legal(self):
        own = assets(("p1", 20_000_000))
        theirs = assets(("q1", 20_500_000))
        result = solve_for(own, theirs, byc=UNRESOLVED)
        trade = build_trade(
            result.packages[0].send_player_ids,
            result.packages[0].receive_player_ids,
            own=own,
            theirs=theirs,
            own_team=team("LAL", 150_000_000),
            partner_team=team("GSW", 150_000_000),
            season=SEASON,
            trade_date=TRADE_DATE,
            re_sign_status=ReSignStatus.UNKNOWN,
        )
        validation = judge(trade)
        with pytest.raises(VerdictUndetermined):
            _ = validation.legal
        assert "UNDETERMINED" in rejection_reason(validation)


class TestBYCResolution:
    def test_an_unsourced_assumption_is_flagged_as_one(self):
        assert RESOLVED.is_assumption is True
        assert BYCResolution("assume_not_re_signed", sourced=True).is_assumption is False
        assert UNRESOLVED.is_assumption is False

    def test_unresolved_means_unknown_not_a_convenient_default(self):
        assert UNRESOLVED.status is ReSignStatus.UNKNOWN


class TestOnlyRulesMayApprove:
    def test_no_agent_or_llm_module_imports_the_validator(self):
        """The charter's first non-negotiable, checked structurally."""
        root = Path(__file__).resolve().parents[1] / "mironba"
        allowed = {"boundary.py", "tick.py", "bench.py", "solver.py"}
        offenders = []
        for part in ("agents", "llm"):
            for path in (root / part).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if "validate_trade" in text and path.name not in allowed:
                    offenders.append(str(path.relative_to(root)))
        assert not offenders, (
            f"modules reaching past the boundary: {offenders}. Only "
            "rules/solver.py and sim/boundary.py may call the validator."
        )

    def test_the_solver_lives_in_rules_not_in_agents(self):
        """Package construction is a deterministic rule, not agent behaviour.

        If it ever moves under agents/ or llm/, the thing deciding what is
        legal has moved to the side of the boundary that may not decide it.
        """
        from mironba.rules import solver

        assert Path(solver.__file__).parent.name == "rules"

    def test_the_verdict_is_never_taken_from_the_model(self):
        """Field names and allowed values, not prose.

        An earlier version scanned the whole serialised schema and tripped over
        the word "validator" inside a docstring. Descriptions are where we
        *explain* that the model does not judge legality, so forbidding the
        vocabulary there is backwards — it is the fields and the enums that
        must not offer a way to say it.
        """
        for schema in AGENT_SCHEMAS:
            spec = schema.model_json_schema()
            names = {name.lower() for name in schema.model_fields}
            values = {
                str(value).lower()
                for prop in (spec.get("properties") or {}).values()
                for value in (prop.get("enum") or [])
            }
            for token in ("approved", "legal", "verdict", "valid"):
                assert not any(token in name for name in names), (
                    f"{schema.__name__} has a {token!r} field"
                )
                assert token not in values, (
                    f"{schema.__name__} offers {token!r} as an allowed value"
                )


# ---------------------------------------------------------------------------
# What the model SEES. Added after M10 found the claim and the check were
# about different surfaces.
# ---------------------------------------------------------------------------

MONEY = re.compile(r"\$\s?\d")


def _sample_context():
    """A GM context with recognisable figures, for rendering."""
    from mironba.agents.gm import GMContext, GMPersona, RosterEntry
    from mironba.rules.cap import ApronTier

    return GMContext(
        team_id="LAL",
        season="2024-25",
        scenario_seed="test",
        own_roster=(RosterEntry("aaa01", "Own Player", 12_345_678),),
        partner_team="GSW",
        partner_roster=(RosterEntry("bbb01", "Their Player", 55_761_216),),
        team_salary=187_502_042,
        tier=ApronTier.FIRST_APRON,
        roster_count=15,
        notes=(),
    ), GMPersona(label="t", risk_tolerance=0.5, win_now_horizon=1, asset_hoarding=0.5)


class TestWhatTheModelSees:
    """The claim is about prompts, so the assertion has to be about prompts.

    ``test_the_model_never_supplies_a_figure`` constrains what the model *emits*
    and every agent-facing schema. Neither says anything about what is rendered
    *into* a prompt, and for four milestones the README claimed "the model never
    sees a salary" while ``CONTEXT_TEMPLATE`` rendered the payroll, the apron
    tier, and every contract on both rosters.
    """

    def test_the_context_prompt_does_carry_money(self):
        """Pinning what is actually true, so the README can match it.

        This is deliberately an assertion that money IS present. The previous
        state of the world was a claim of absence with no test either way; a
        test that records the real behaviour is what stops the claim drifting
        back.
        """
        from mironba.agents.gm import render_context

        context, persona = _sample_context()
        rendered = render_context(context, persona)
        assert MONEY.search(rendered), "context prompt no longer carries money"
        assert "187,502,042" in rendered, "team payroll is visible to the model"
        assert "12,345,678" in rendered, "own-roster salaries are visible"
        assert "55,761,216" in rendered, "partner-roster salaries are visible"
        assert "first apron" in rendered, "apron status is visible"

    def test_every_roster_line_carries_a_salary(self):
        from mironba.agents.gm import RosterEntry

        assert MONEY.search(RosterEntry("x", "Y", 1_000_000).render())

    def test_the_readme_claim_matches_the_rendered_surface(self):
        """The README may not claim salary-blindness while this renders money.

        Enforces the charter rule added at M10: no claim without a test that
        asserts the surface the claim is actually about.
        """
        from pathlib import Path

        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(
            encoding="utf-8"
        )
        # A retraction may quote the old claim; an assertion may not make it.
        # Distinguished by line, so the correction note stays legal and any
        # reinstatement of the claim does not.
        RETRACTION = ("used to read", "was false", "no longer", "entry 21")
        offenders = [
            line.strip()
            for line in readme.splitlines()
            if "sees a salary" in line
            and not any(marker in line for marker in RETRACTION)
        ]
        assert not offenders, (
            f"README claims salary-blindness: {offenders}. render_context() "
            "emits team payroll, apron status and every contract on both "
            "rosters. Say what is true, not the nearest true-sounding thing."
        )

    def test_the_model_still_cannot_emit_a_package_or_terms(self):
        """The half of the claim that survived, kept explicit.

        Seeing a salary and being able to *set* one are different powers. The
        first is what M10 found; the second is what the architecture actually
        prevents, and it is still prevented.
        """
        from mironba.agents.gm import PackageSelection, TradeIntent

        for model in (TradeIntent, PackageSelection):
            for name, field in model.model_fields.items():
                annotation = str(field.annotation).lower()
                assert "float" not in annotation or "salary" not in name.lower()
                assert "salary" not in name.lower(), (
                    f"{model.__name__}.{name} would let the model state terms"
                )
