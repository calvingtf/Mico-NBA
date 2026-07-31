"""The GM agent, offline.

A fake client returns scripted schema objects, so the two-step control flow and
the persona rules can be asserted without a model. What a live model actually
does with these prompts is a separate question, measured by
`python -m mironba.sim.bench` and reported in the README.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from mironba.agents.base import Agent, Persona, PersonaError
from mironba.agents.gm import (
    TEMPLATES,
    GMAgent,
    GMContext,
    GMPersona,
    RosterEntry,
    render_context,
)
from mironba.llm.schemas import ActionChoice, TradeProposal
from mironba.rules.cap import ApronTier
from mironba.world.events import EventLog, EventType
from mironba.world.manifest import Run, build_manifest, template_hash


class FakeClient:
    """Returns scripted objects and records the schema it was asked for."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, schema=None, profile="default", *, purpose=""):
        self.calls.append(
            {"schema": schema, "purpose": purpose, "messages": messages, "profile": profile}
        )
        return self.script.pop(0)


@pytest.fixture
def log(tmp_path):
    manifest = build_manifest(
        model_id="m",
        server="fake",
        base_url="http://x",
        prompt_template_hash="h",
        snapshot_date="2026-07-30",
    )
    return EventLog(Run.start(manifest, runs_dir=tmp_path))


@pytest.fixture
def context():
    return GMContext(
        team_id="LAL",
        season="2024-25",
        scenario_seed="Warriors will listen on Curry.",
        own_roster=(RosterEntry("p1", "One", 40_000_000),),
        partner_team="GSW",
        partner_roster=(RosterEntry("q1", "Two", 55_761_216),),
        team_salary=180_000_000,
        tier=ApronTier.FIRST_APRON,
        roster_count=14,
    )


PERSONA = GMPersona(
    label="win-now", risk_tolerance=0.8, win_now_horizon=1, asset_hoarding=0.2
)


def agent(client, log):
    return GMAgent("LAL", PERSONA, client, log, profile="gm_agent")


class TestTwoStepSelection:
    def test_a_decision_is_two_calls_never_one(self, log, context):
        """The charter's rule. A nested trade-inside-decision is the shape
        small models fail on, and the failure is unattributable: you cannot
        tell a bad choice from bad typing."""
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeProposal(
                    partner_team="GSW",
                    send_player_ids=["p1"],
                    receive_player_ids=["q1"],
                    reason="star",
                ),
            ]
        )
        decision = agent(client, log).decide(context)
        assert decision.action == "propose_trade"
        assert [c["schema"] for c in client.calls] == [ActionChoice, TradeProposal]

    def test_standing_pat_costs_one_call(self, log, context):
        """No parameters to fill, so no second call."""
        client = FakeClient([ActionChoice(action="stand_pat", reason="roster is fine")])
        decision = agent(client, log).decide(context)
        assert decision.action == "stand_pat"
        assert len(client.calls) == 1
        assert decision.proposal is None

    def test_the_first_schema_offers_only_the_action_enum(self, log, context):
        client = FakeClient([ActionChoice(action="stand_pat", reason="x")])
        agent(client, log).decide(context)
        props = client.calls[0]["schema"].model_json_schema()["properties"]
        assert set(props) == {"action", "reason"}

    def test_the_agent_names_a_role_not_a_model(self, log, context):
        client = FakeClient([ActionChoice(action="stand_pat", reason="x")])
        agent(client, log).decide(context)
        assert client.calls[0]["profile"] == "gm_agent"


class TestContextIsBounded:
    def test_the_agent_sees_only_its_team_and_the_named_partner(self, context):
        """No league-wide context in M1."""
        rendered = render_context(context, PERSONA)
        assert "LAL" in rendered and "GSW" in rendered
        for other in ("BOS", "MIA", "DEN", "NYK", "PHX"):
            assert other not in rendered

    def test_the_seed_reaches_the_prompt(self, context):
        assert "Curry" in render_context(context, PERSONA)

    def test_payroll_caveats_travel_with_the_number(self, context):
        """The model is told the payroll is approximate, where it reads it."""
        annotated = replace(context, notes=("Payroll is a sum of season cap hits.",))
        assert "Note: Payroll is a sum" in render_context(annotated, PERSONA)

    def test_the_persona_appears_as_numbers(self):
        block = PERSONA.as_prompt_block()
        assert "risk_tolerance: 0.8" in block
        assert "win-now" not in block  # the label is for logs, not the model


class TestPersona:
    def test_prose_personas_are_rejected(self):
        """An explicit charter anti-goal.

        A paragraph of characterisation cannot be swept, cannot be fed to
        rules/, and cannot be compared across models in M5.
        """

        @dataclass(frozen=True, slots=True)
        class ProsePersona(Persona):
            backstory: str = "A grizzled executive who loves veterans."

        with pytest.raises(PersonaError, match="numeric"):
            ProsePersona().validate()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"risk_tolerance": 1.5},
            {"risk_tolerance": -0.1},
            {"asset_hoarding": 2.0},
            {"win_now_horizon": 0},
            {"win_now_horizon": 99},
        ],
    )
    def test_out_of_range_parameters_are_rejected(self, kwargs):
        with pytest.raises(ValueError):
            GMPersona(**kwargs).validate()

    def test_asset_hoarding_maps_to_a_hard_limit(self):
        assert GMPersona(asset_hoarding=0.9).max_assets_out == 1
        assert GMPersona(asset_hoarding=0.5).max_assets_out == 2
        assert GMPersona(asset_hoarding=0.1).max_assets_out == 4

    def test_an_agent_validates_its_persona_on_construction(self, log):
        with pytest.raises(ValueError):
            GMAgent("LAL", GMPersona(risk_tolerance=5.0), FakeClient([]), log)


class TestRetry:
    def test_the_rejection_reason_reaches_the_model_verbatim(self, log, context):
        client = FakeClient(
            [
                TradeProposal(
                    partner_team="GSW",
                    send_player_ids=["p1"],
                    receive_player_ids=["q1"],
                    reason="revised",
                )
            ]
        )
        previous = TradeProposal(
            partner_team="GSW",
            send_player_ids=["p1"],
            receive_player_ids=["q1"],
            reason="first",
        )
        agent(client, log).revise(
            context, previous, "- SALARY_MATCH: LAL may take back at most $1,234,567"
        )
        prompt = client.calls[0]["messages"][-1]["content"]
        assert "$1,234,567" in prompt
        assert "REJECTED" in prompt

    def test_the_retry_is_marked_as_a_distinct_purpose(self, log, context):
        """So the raw log can separate first attempts from revisions."""
        client = FakeClient(
            [TradeProposal(
                partner_team="GSW", send_player_ids=["p1"],
                receive_player_ids=["q1"], reason="r",
            )]
        )
        previous = TradeProposal(
            partner_team="GSW", send_player_ids=["p1"],
            receive_player_ids=["q1"], reason="f",
        )
        agent(client, log).revise(context, previous, "reason")
        assert client.calls[0]["purpose"] == "trade_proposal_retry"


class TestEventLogging:
    def test_every_step_lands_in_the_log(self, log, context):
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeProposal(
                    partner_team="GSW", send_player_ids=["p1"],
                    receive_player_ids=["q1"], reason="star",
                ),
            ]
        )
        agent(client, log).decide(context)
        types = [e.type for e in log]
        assert EventType.AGENT_PROMPTED in types
        assert EventType.AGENT_ACTION_CHOSEN in types
        assert EventType.AGENT_PROPOSED in types

    def test_a_proposal_is_public_and_reasoning_is_internal(self, log, context):
        """One log with a visibility field, per the charter's anti-goals."""
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeProposal(
                    partner_team="GSW", send_player_ids=["p1"],
                    receive_player_ids=["q1"], reason="star",
                ),
            ]
        )
        agent(client, log).decide(context)
        chosen = log.of_type(EventType.AGENT_ACTION_CHOSEN)[0]
        proposed = log.of_type(EventType.AGENT_PROPOSED)[0]
        assert chosen.visibility == "internal"
        assert proposed.visibility == "public"


class TestPromptTemplates:
    def test_the_hash_covers_every_template(self):
        assert len(TEMPLATES) == 5
        assert template_hash(*TEMPLATES) == template_hash(*TEMPLATES)

    def test_rewording_any_template_changes_the_hash(self):
        for i in range(len(TEMPLATES)):
            altered = list(TEMPLATES)
            altered[i] = altered[i] + " "
            assert template_hash(*altered) != template_hash(*TEMPLATES)

    def test_the_system_prompt_tells_the_model_it_does_not_judge_legality(self):
        from mironba.agents.gm import SYSTEM_TEMPLATE

        assert "do not decide whether a trade is legal" in SYSTEM_TEMPLATE.lower()
