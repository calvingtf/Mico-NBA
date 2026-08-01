"""Claude Agent SDK as a transport, for presentation agents only.

A second route to the same models, authenticated by the local Claude Code
subscription rather than an API key. It exists because the report and chat
agents are *presentation*: their limitation blocks are module constants
appended after the model speaks, nobody re-runs a prose summary to verify it,
and so nothing about their correctness depends on sampling.

## Why measurement may not use this

The SDK exposes **no temperature, no seed, no top_p and no max_tokens** — only
``effort`` and thinking configuration. Audited against
``dataclasses.fields(ClaudeAgentOptions)``, not assumed.

Missing ``seed`` breaks reproducibility of a Claude run *against itself*, which
is a charter non-negotiable rather than a comparability nicety. Missing
``max_tokens`` reintroduces the truncation that produced four false schema
failures at M1.5 — completions cut mid-JSON, counted as the model failing to
fill a form it was filling correctly.

So measurement runs on :mod:`mironba.llm.providers.anthropic` over HTTP with an
API key, and :func:`assert_not_measurement_profile` makes the split
enforceable rather than a convention.

## The tool restriction, which is not what it looks like

``allowed_tools=[]`` does **not** disable tools. Measured: with it set, the
init payload still advertises 31 tools including ``Bash``, ``Edit``, ``Write``
and ``Glob``, and the working directory is whatever the process started in —
which for this project is the repository. It is a *permission filter* over a
registry, not the registry.

``tools=[]`` is the mechanism that empties the registry. ``setting_sources=[]``
additionally stops project hooks from firing (4 hook events became 0).

Both are set here, plus ``allowed_tools=[]`` as belt and braces, and
``cwd`` is forced to a scratch directory so that even a future SDK change that
re-populated the registry could not reach the repository.
``tests/test_sdk_transport.py`` fails if any tool appears.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from typing import Any

from mironba.llm.providers.base import (
    ModelInfo,
    ProviderError,
    RawCompletion,
    RuntimeInfo,
    SamplingParams,
)

#: Nothing may execute. Asserted from the init payload, not trusted.
NO_TOOLS: list[str] = []

#: Tools whose presence would mean this transport can touch the repository.
#: Named explicitly so the test reads as a statement about risk.
FORBIDDEN_TOOLS = frozenset({
    "Bash", "PowerShell", "Edit", "Write", "NotebookEdit",
    "Glob", "Grep", "Read", "Task", "WebFetch", "WebSearch",
})

#: Sampling parameters this transport cannot set. Recorded in the manifest as
#: null with this reason attached - never as the value we would have liked, and
#: never as a silent default.
UNSETTABLE = ("temperature", "top_p", "seed", "max_tokens")
UNSETTABLE_REASON = (
    "not exposed by Agent SDK (ClaudeAgentOptions has no temperature/seed/"
    "top_p/max_tokens; only effort and thinking config). Recorded null rather "
    "than as an unset default."
)

#: Profiles that may never use this transport. Measurement needs a seed.
MEASUREMENT_MODULES = ("mironba.eval", "mironba.sim")


class SdkUnavailable(ProviderError):
    """The SDK is not installed, or refused to authenticate."""


def assert_not_measurement_profile(caller_module: str, profile: str) -> None:
    """Refuse an SDK profile from measurement code.

    A convention that lives only in a docstring gets broken by the person who
    did not read it. Measurement without a seed is not reproducible, and the
    charter says every run must be.
    """
    if any(caller_module.startswith(m) for m in MEASUREMENT_MODULES):
        raise ProviderError(
            f"{caller_module} may not use the SDK transport (profile "
            f"{profile!r}). It exposes no seed or temperature, so a run under "
            "it cannot be reproduced - see the charter's reproducibility rule. "
            "Measurement uses the HTTP provider with an API key."
        )


class ClaudeSdkProvider:
    """Subscription-authenticated transport. Presentation agents only."""

    name = "claude_sdk"
    transport = "sdk"
    auth_route = "subscription"

    def enforces_schema(self) -> bool:
        """Unknown until probed, and this transport cannot force a tool call.

        The HTTP provider constrains decoding with a forced ``tool_choice``.
        Nothing equivalent is reachable here, so the schema is asked for in the
        prompt and validated afterwards. Reporting False keeps the client's
        prompt-side schema defences switched on.
        """
        return False

    def _options(self, params: SamplingParams, schema: dict[str, Any] | None):
        from claude_agent_sdk import ClaudeAgentOptions

        # cwd is a fresh scratch directory, never the repository. Defence in
        # depth: tools=[] already empties the registry, and this makes the
        # blast radius empty even if that stopped working.
        jail = tempfile.mkdtemp(prefix="mironba-sdk-")
        kwargs: dict[str, Any] = {
            "tools": NO_TOOLS,          # the registry. allowed_tools is NOT this.
            "allowed_tools": [],        # belt and braces
            "setting_sources": [],      # no project hooks, no project settings
            "cwd": jail,
            "max_turns": 1,             # single turn; no resume, no continuation
        }
        if params.thinking:
            kwargs["thinking"] = {"type": "enabled"}
        return ClaudeAgentOptions(**kwargs), jail

    def chat(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        params: SamplingParams,
        timeout: float,
    ) -> RawCompletion:
        try:
            from claude_agent_sdk import query
        except ImportError as exc:  # pragma: no cover
            raise SdkUnavailable(
                "claude-agent-sdk is not installed. pip install claude-agent-sdk"
            ) from exc

        system = "\n\n".join(m["content"] for m in messages if m.get("role") == "system")
        body = "\n\n".join(
            m["content"] for m in messages if m.get("role") in ("user", "assistant")
        )
        prompt = f"{system}\n\n{body}" if system else body
        if schema is not None:
            # No forced tool call is available here, so the schema goes in the
            # prompt and client.py validates. Same defence the local models get
            # when a server will not constrain decoding.
            prompt += (
                "\n\nReply with JSON only, matching this schema exactly. "
                "No prose, no code fence.\n" + json.dumps(schema, indent=2)
            )

        options, _jail = self._options(params, schema)

        async def run() -> tuple[str, dict, str]:
            text, usage, stop = "", {}, "end_turn"
            async for message in query(prompt=prompt, options=options):
                kind = type(message).__name__
                if kind == "SystemMessage":
                    advertised = (getattr(message, "data", {}) or {}).get("tools") or []
                    leaked = FORBIDDEN_TOOLS & set(advertised)
                    if leaked:
                        raise ProviderError(
                            f"SDK advertised tools this transport must not have: "
                            f"{sorted(leaked)}. Refusing to continue."
                        )
                elif kind == "ResultMessage":
                    text = str(getattr(message, "result", "") or "")
                    usage = getattr(message, "usage", {}) or {}
                    usage["total_cost_usd"] = getattr(message, "total_cost_usd", None)
                    stop = str(getattr(message, "subtype", "") or "end_turn")
            return text, usage, stop

        started = time.monotonic()
        text, usage, stop = asyncio.run(run())
        latency = time.monotonic() - started

        return RawCompletion(
            text=text.strip(),
            latency_s=latency,
            model=model,
            finish_reason=stop,
            usage={
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                "total_cost_usd": usage.get("total_cost_usd"),
            },
            raw={"transport": self.transport, "auth_route": self.auth_route},
        )

    def list_models(self, base_url: str = "", timeout: float = 10.0) -> list[ModelInfo]:
        """Empty, and honestly so.

        The SDK holds the credential inside the CLI and exposes no model
        listing. Returning a hardcoded list would be inventing an inventory;
        an empty list says "ask the HTTP provider", which does query /v1/models.
        """
        return []

    def model_info(self, base_url: str, model: str, timeout: float = 30) -> ModelInfo:
        return ModelInfo(
            model_id=model,
            quantization="not-applicable (hosted)",
            families=("anthropic", "sdk"),
        )

    def runtime_info(self, base_url: str, model: str, timeout: float = 30) -> RuntimeInfo:
        """All None. No local weights; see the anthropic provider's note."""
        return RuntimeInfo()
