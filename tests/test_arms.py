"""The A/B arms, offline.

The arm is an experiment condition, so the properties worth pinning are about
what reaches the prompt rather than about what the model does with it. Three
claims:

  * the blind arm is byte-identical to the pre-M1.6 prompt, or the baseline is
    not a baseline;
  * the feasible arm adds names and no figures;
  * the arm is recorded, because two runs can now differ in nothing else.
"""

from __future__ import annotations

import re

import pytest

from mironba.agents.gm import (
    ARMS,
    INTENT_TEMPLATE,
    GMAgent,
    GMContext,
    GMPersona,
    RosterEntry,
    render_context,
)
from mironba.llm.schemas import ActionChoice, TradeIntent
from mironba.rules.cap import ApronTier
from mironba.rules.solver import FeasibleTarget
from mironba.world.events import EventLog, EventType
from mironba.world.manifest import Run, build_manifest


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def complete(self, messages, schema=None, profile="default", *, purpose=""):
        self.calls.append({"schema": schema, "purpose": purpose, "messages": messages})
        return self.script.pop(0)

    @property
    def prompts(self) -> list[str]:
        return [
            "\n".join(m["content"] for m in call["messages"]) for call in self.calls
        ]


@pytest.fixture
def log(tmp_path):
    return EventLog(
        Run.start(
            build_manifest(
                model_id="m", server="fake", base_url="http://x",
                prompt_template_hash="h", snapshot_date="2026-07-30",
            ),
            runs_dir=tmp_path,
        )
    )


PERSONA = GMPersona(
    label="balanced", risk_tolerance=0.5, win_now_horizon=3, asset_hoarding=0.5
)

FEASIBLE = (
    FeasibleTarget("q1", "Cheap Guy", 5, 1),
    FeasibleTarget("q2", "Cheaper Guy", 3, 1),
)


def context(feasible=()):
    return GMContext(
        team_id="CHI",
        season="2024-25",
        scenario_seed="Utah is selling.",
        own_roster=(RosterEntry("p1", "One", 20_000_000),),
        partner_team="UTA",
        partner_roster=(
            RosterEntry("q1", "Cheap Guy", 18_350_000),
            RosterEntry("q2", "Cheaper Guy", 14_092_577),
            RosterEntry("q3", "Dear Guy", 42_176_400),
        ),
        team_salary=165_919_354,
        tier=ApronTier.OVER_CAP,
        roster_count=15,
        feasible_targets=feasible,
    )


def script():
    return [
        ActionChoice(action="propose_trade", reason="upgrade"),
        TradeIntent(
            target_player_ids=["q1"], tradeable_asset_ids=["p1"], reason="fit"
        ),
    ]


def run(arm, feasible, log):
    client = FakeClient(script())
    agent = GMAgent("CHI", PERSONA, client, log, profile="gm_agent", arm=arm)
    ctx = context(feasible)
    agent.decide(ctx)
    return agent, client, ctx


class TestTheArmsDiffer:
    def test_the_blind_arm_never_mentions_the_list(self, log):
        """Checked on the list's own markers, not on the names.

        The names are in the blind prompt too — the partner roster block has
        always printed them. What the blind arm must not carry is any statement
        that a particular player is *gettable*.
        """
        _, client, _ = run("unaided", FEASIBLE, log)
        intent_prompt = client.prompts[1]
        assert "legally acquire" not in intent_prompt
        assert "ways, from" not in intent_prompt
        assert "cannot be acquired" not in intent_prompt

    def test_the_unaided_prompt_is_the_pre_m16_prompt_unchanged(self, log):
        """The baseline has to be the old thing, not a near-copy of it.

        If the blind arm drifted, the delta would be measuring two changes at
        once and attributing both to the feasible list.
        """
        _, client, ctx = run("unaided", FEASIBLE, log)
        expected = INTENT_TEMPLATE.format(
            context=render_context(ctx, PERSONA),
            reason="upgrade",
            partner_team="UTA",
        )
        assert expected in client.prompts[1]

    def test_the_feasible_arm_names_every_listed_target(self, log):
        _, client, _ = run("feasible", FEASIBLE, log)
        intent_prompt = client.prompts[1]
        for target in FEASIBLE:
            assert target.player_id in intent_prompt
            assert target.name in intent_prompt

    def test_the_feasible_arm_omits_the_unreachable_target(self, log):
        """`q3` is on the partner roster and not on the list. It still appears
        in the roster block — the model can see he exists — but nothing tells
        it he is gettable."""
        _, client, _ = run("feasible", FEASIBLE, log)
        section = client.prompts[1].split("legally acquire")[1].split("Anyone not")[0]
        assert "q3" not in section


class TestTheFeasibleArmLeaksNoMoney:
    def test_the_added_block_carries_no_salary(self, log):
        """The roster block already prints salaries and always has. What must
        not happen is the *feasible list* adding a price next to a name, which
        is the one place a model could pair a target with a figure."""
        _, client, _ = run("feasible", FEASIBLE, log)
        prompt = client.prompts[1]
        block = prompt.split("legally acquire")[1].split("Anyone not")[0]
        assert all(int(n) < 1000 for n in re.findall(r"\d+", block)), block

    def test_no_partner_salary_appears_beside_a_feasible_name(self, log):
        _, client, _ = run("feasible", FEASIBLE, log)
        block = client.prompts[1].split("legally acquire")[1].split("Anyone not")[0]
        for salary in ("18,350,000", "14,092,577", "42,176,400"):
            assert salary not in block


class TestEmptyListFallsBack:
    def test_an_empty_list_uses_the_blind_prompt(self, log):
        """Printing "here is who you can get: (nothing)" invites the model to
        invent a name, and dresses up a fact about the team as a withheld
        list."""
        agent, client, ctx = run("feasible", (), log)
        assert agent.shows_feasible(ctx) is False
        assert "legally acquire" not in client.prompts[1]

    def test_shows_feasible_needs_both_the_arm_and_a_list(self, log):
        assert run("feasible", FEASIBLE, log)[0].shows_feasible(context(FEASIBLE))
        assert not run("unaided", FEASIBLE, log)[0].shows_feasible(context(FEASIBLE))
        assert not run("feasible", (), log)[0].shows_feasible(context(()))


class TestTheArmIsRecorded:
    def test_the_prompt_event_records_arm_and_whether_it_was_shown(self, log):
        run("feasible", FEASIBLE, log)
        prompted = [
            e for e in log.of_type(EventType.AGENT_PROMPTED)
            if e.payload.get("step") == "trade_intent"
        ]
        assert prompted[0].payload["arm"] == "feasible"
        assert prompted[0].payload["feasible_shown"] is True
        assert prompted[0].payload["feasible_count"] == 2

    def test_an_unaided_run_still_records_the_list_it_withheld(self, log):
        """Without this the blind arm cannot report how often its model asked
        for someone unreachable, and that baseline is the whole comparison."""
        run("unaided", FEASIBLE, log)
        prompted = [
            e for e in log.of_type(EventType.AGENT_PROMPTED)
            if e.payload.get("step") == "trade_intent"
        ]
        assert prompted[0].payload["feasible_shown"] is False
        assert prompted[0].payload["feasible_count"] == 2

    def test_an_unknown_arm_is_refused(self, log):
        with pytest.raises(ValueError, match="arm must be one of"):
            GMAgent("CHI", PERSONA, FakeClient([]), log, arm="whatever")

    def test_the_default_arm_is_the_old_behaviour(self, log):
        """A caller that has not been updated keeps measuring what it was."""
        agent = GMAgent("CHI", PERSONA, FakeClient([]), log)
        assert agent.arm == "unaided"
        assert ARMS[0] == "unaided"
