"""The GM agent, offline.

A fake client returns scripted schema objects, so the three-step control flow and
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
from mironba.llm.schemas import ActionChoice, PackageSelection, TradeIntent
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


class TestThreeStepFlow:
    def test_a_decision_is_two_calls_never_one(self, log, context):
        """Action first, then an intent. Never one nested call.

        A small model asked to emit a decision *containing* a structure fails
        more often and unattributably: you cannot tell a bad choice from bad
        typing.
        """
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeIntent(
                    target_player_ids=["q1"],
                    tradeable_asset_ids=["p1"],
                    reason="star",
                ),
            ]
        )
        decision = agent(client, log).decide(context)
        assert decision.action == "propose_trade"
        assert [c["schema"] for c in client.calls] == [ActionChoice, TradeIntent]

    def test_the_agent_emits_an_intent_not_a_package(self, log, context):
        """The M1.5 architecture, at the agent's own boundary."""
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeIntent(
                    target_player_ids=["q1"],
                    tradeable_asset_ids=["p1"],
                    reason="star",
                ),
            ]
        )
        decision = agent(client, log).decide(context)
        assert decision.intent.target_player_ids == ["q1"]
        assert not hasattr(decision, "proposal")

    def test_standing_pat_costs_one_call(self, log, context):
        """No parameters to fill, so no second call."""
        client = FakeClient([ActionChoice(action="stand_pat", reason="roster is fine")])
        decision = agent(client, log).decide(context)
        assert decision.action == "stand_pat"
        assert len(client.calls) == 1
        assert decision.intent is None

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


class TestIntentRevision:
    def test_the_binding_constraint_reaches_the_model_verbatim(self, log, context):
        """The retry that is worth something.

        The old one handed back a rejection of a package the model had guessed
        at and asked it to guess again; nine of those rescued nothing. This one
        hands back which rule bound and by how many dollars, and asks for a
        different want.
        """
        client = FakeClient(
            [TradeIntent(target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="r")]
        )
        previous = TradeIntent(
            target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="first"
        )
        agent(client, log).revise_intent(
            context, previous, "SALARY_MATCH: you were $1,234,567 short"
        )
        prompt = client.calls[0]["messages"][-1]["content"]
        assert "$1,234,567" in prompt
        assert "NO legal package" in prompt

    def test_the_revision_is_marked_as_a_distinct_purpose(self, log, context):
        client = FakeClient(
            [TradeIntent(target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="r")]
        )
        previous = TradeIntent(
            target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="f"
        )
        agent(client, log).revise_intent(context, previous, "reason")
        assert client.calls[0]["purpose"] == "trade_intent_retry"


class TestPackageSelection:
    def test_the_model_picks_an_index(self, log, context):
        client = FakeClient([PackageSelection(selection=1, reason="best value")])
        choice = agent(client, log).select_package(context, "  [0] a\n  [1] b", 2)
        assert choice.selection == 1
        assert not choice.declined

    def test_declining_all_is_expressible(self, log, context):
        client = FakeClient([PackageSelection(selection=-1, reason="none help")])
        assert agent(client, log).select_package(context, "  [0] a", 1).declined

    def test_an_out_of_range_index_is_read_as_a_decline(self, log, context):
        """Never clamped onto a package the model did not choose.

        Clamping would attribute a trade to a GM that it never picked, and the
        event log would record a decision nobody made.
        """
        client = FakeClient([PackageSelection(selection=7, reason="oops")])
        choice = agent(client, log).select_package(context, "  [0] a", 1)
        assert choice.declined
        assert log.of_type(EventType.SELECTION_OUT_OF_RANGE)

    def test_the_options_are_shown_as_legal_not_as_candidates(self, log, context):
        client = FakeClient([PackageSelection(selection=0, reason="fine")])
        agent(client, log).select_package(context, "  [0] a", 1)
        prompt = client.calls[0]["messages"][-1]["content"]
        assert "LEGAL" in prompt
        assert "basketball merit" in prompt


class TestEventLogging:
    def test_every_step_lands_in_the_log(self, log, context):
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeIntent(
                    target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="star"
                ),
            ]
        )
        agent(client, log).decide(context)
        types = [e.type for e in log]
        assert EventType.AGENT_PROMPTED in types
        assert EventType.AGENT_ACTION_CHOSEN in types
        assert EventType.AGENT_INTENT in types

    def test_intent_is_internal_and_selection_is_public(self, log, context):
        """One log with a visibility field, per the charter's anti-goals.

        An intent is a GM's private wish list; a selected package is what the
        league would see.
        """
        client = FakeClient(
            [
                ActionChoice(action="propose_trade", reason="win now"),
                TradeIntent(
                    target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="star"
                ),
            ]
        )
        gm = agent(client, log)
        gm.decide(context)
        assert log.of_type(EventType.AGENT_INTENT)[0].visibility == "internal"

        client.script = [PackageSelection(selection=0, reason="fine")]
        gm.select_package(context, "  [0] a", 1)
        assert log.of_type(EventType.AGENT_SELECTED)[0].visibility == "public"


class TestPromptTemplates:
    def test_the_hash_covers_every_template(self):
        assert len(TEMPLATES) == 6
        assert template_hash(*TEMPLATES) == template_hash(*TEMPLATES)

    def test_rewording_any_template_changes_the_hash(self):
        for i in range(len(TEMPLATES)):
            altered = list(TEMPLATES)
            altered[i] = altered[i] + " "
            assert template_hash(*altered) != template_hash(*TEMPLATES)

    def test_the_system_prompt_tells_the_model_it_does_not_judge_legality(self):
        from mironba.agents.gm import SYSTEM_TEMPLATE

        assert "do not decide whether a trade is legal" in SYSTEM_TEMPLATE.lower()
