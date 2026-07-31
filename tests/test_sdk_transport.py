"""The SDK transport must not be able to touch this repository.

``allowed_tools=[]`` reads like it disables tools. It does not. Measured against
the live SDK: with it set, the init payload still advertises **31 tools**
including ``Bash``, ``Edit``, ``Write``, ``Read`` and ``Glob``, and the working
directory is whatever the process started in — the repository. It is a
permission filter over a registry, not the registry itself.

``tools=[]`` empties the registry, and ``setting_sources=[]`` stops project
hooks firing. Both are set, and these tests fail if either stops working.

The live check is marked ``sdk`` and skipped by default: it spends subscription
quota. The configuration tests below need no network and always run, which is
what actually guards the repo — a wrong config is caught before anything is
launched.
"""

from __future__ import annotations

import inspect

import pytest

from mironba.llm.providers.base import ProviderError, SamplingParams
from mironba.llm.providers.claude_sdk import (
    FORBIDDEN_TOOLS,
    MEASUREMENT_MODULES,
    NO_TOOLS,
    UNSETTABLE,
    UNSETTABLE_REASON,
    ClaudeSdkProvider,
    assert_not_measurement_profile,
)


@pytest.fixture
def provider():
    return ClaudeSdkProvider()


class TestToolRegistryIsEmpty:
    def test_the_options_set_tools_not_just_allowed_tools(self, provider):
        """The distinction this whole module exists for.

        ``allowed_tools`` alone left Bash, Edit and Write live. If a future
        edit drops ``tools``, this fails.
        """
        pytest.importorskip("claude_agent_sdk")
        options, _ = provider._options(SamplingParams(), None)
        assert options.tools == [], "tools=[] is the registry; allowed_tools is not"
        assert options.allowed_tools == []

    def test_project_settings_and_hooks_are_not_loaded(self, provider):
        """setting_sources=[] took hook events from 4 to 0."""
        pytest.importorskip("claude_agent_sdk")
        options, _ = provider._options(SamplingParams(), None)
        assert options.setting_sources == []

    def test_the_working_directory_is_never_the_repository(self, provider):
        """Defence in depth: an empty registry plus an empty blast radius."""
        pytest.importorskip("claude_agent_sdk")
        import pathlib

        _, jail = provider._options(SamplingParams(), None)
        repo = pathlib.Path(__file__).resolve().parents[1]
        assert repo not in pathlib.Path(jail).resolve().parents
        assert pathlib.Path(jail).resolve() != repo

    def test_single_turn_only(self, provider):
        pytest.importorskip("claude_agent_sdk")
        options, _ = provider._options(SamplingParams(), None)
        assert options.max_turns == 1

    def test_the_forbidden_list_covers_everything_that_touches_disk(self):
        for tool in ("Bash", "PowerShell", "Edit", "Write", "Read", "Glob",
                     "Grep", "NotebookEdit", "Task"):
            assert tool in FORBIDDEN_TOOLS

    def test_a_leaked_tool_raises_rather_than_proceeding(self, provider):
        """The runtime half: if the init payload ever advertises a tool, the
        call aborts instead of running with it."""
        source = inspect.getsource(provider.chat)
        assert "FORBIDDEN_TOOLS" in source
        assert "raise ProviderError" in source

    def test_no_tools_constant_is_empty(self):
        assert NO_TOOLS == []


class TestMeasurementCannotUseIt:
    @pytest.mark.parametrize("module", ["mironba.eval.backtest",
                                        "mironba.sim.deadline",
                                        "mironba.sim.tick"])
    def test_measurement_modules_are_refused(self, module):
        with pytest.raises(ProviderError, match="may not use the SDK transport"):
            assert_not_measurement_profile(module, "sdk_opus")

    @pytest.mark.parametrize("module", ["mironba.agents.report",
                                        "mironba.agents.chat"])
    def test_presentation_modules_are_allowed(self, module):
        assert_not_measurement_profile(module, "sdk_opus") is None

    def test_the_guarded_list_names_eval_and_sim(self):
        assert "mironba.eval" in MEASUREMENT_MODULES
        assert "mironba.sim" in MEASUREMENT_MODULES

    def test_the_refusal_says_why_rather_than_just_refusing(self):
        """The message has to teach, not just block.

        A guard that says "not allowed" gets worked around; one that says
        "no seed, so the run is not reproducible" gets understood.
        """
        with pytest.raises(ProviderError) as caught:
            assert_not_measurement_profile("mironba.eval.backtest", "sdk_opus")
        message = str(caught.value).lower()
        assert "seed" in message
        assert "reproduc" in message
        assert "seed" in UNSETTABLE


class TestSamplingParamsAreRecordedAsNull:
    def test_the_unsettable_list_matches_what_the_sdk_lacks(self):
        """Audited from dataclasses.fields(ClaudeAgentOptions), not assumed."""
        pytest.importorskip("claude_agent_sdk")
        import dataclasses

        from claude_agent_sdk import ClaudeAgentOptions

        names = {f.name for f in dataclasses.fields(ClaudeAgentOptions)}
        for param in UNSETTABLE:
            assert param not in names, (
                f"{param} IS exposed by the SDK now - if so, this transport "
                "could become measurement-capable and the guard should be "
                "revisited rather than left in place out of habit."
            )

    def test_the_reason_explains_null_rather_than_default(self):
        assert "not exposed" in UNSETTABLE_REASON
        assert "null" in UNSETTABLE_REASON.lower()

    def test_the_provider_does_not_claim_schema_enforcement(self, provider):
        """No forced tool call is reachable here, unlike the HTTP path."""
        assert provider.enforces_schema() is False


@pytest.mark.sdk
class TestLiveInitPayload:
    """Spends subscription quota. Run with -m sdk."""

    def test_the_live_init_payload_advertises_no_tools(self):
        pytest.importorskip("claude_agent_sdk")
        import asyncio
        import os
        import tempfile

        from claude_agent_sdk import ClaudeAgentOptions, query

        jail = tempfile.mkdtemp(prefix="mironba-sdk-test-")

        async def run():
            options = ClaudeAgentOptions(
                tools=[], allowed_tools=[], setting_sources=[],
                cwd=jail, max_turns=1,
            )
            advertised = None
            async for message in query(prompt="Reply with exactly: OK", options=options):
                if type(message).__name__ == "SystemMessage":
                    advertised = (getattr(message, "data", {}) or {}).get("tools")
            return advertised

        tools = asyncio.run(run())
        assert tools is not None, "no init payload seen"
        assert not (FORBIDDEN_TOOLS & set(tools)), f"leaked: {sorted(set(tools))}"
        assert tools == [], f"expected an empty registry, got {tools}"
