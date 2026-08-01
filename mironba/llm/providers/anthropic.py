"""Anthropic Messages API, as a provider.

The charter allows exactly one place for provider-specific code and this is it.
Anthropic is the "anything exotic" case it anticipated: the endpoint is
``/v1/messages`` rather than ``/v1/chat/completions``, the system prompt is a
top-level field rather than a message, the key rides in ``x-api-key`` rather
than a bearer header, and structured output is a *tool definition* rather than
``response_format``.

## Structured output is a forced tool call, not response_format

Writing ``response_format: {"type": "json_schema"}`` to this API does nothing —
the field is ignored, the model returns prose, and the pydantic parse fails.
The result would look like a capability finding about the model and would be an
adapter bug.

The mechanism is: declare a single tool whose ``input_schema`` is the pydantic
schema, then set ``tool_choice`` to that tool by name. The model must emit a
``tool_use`` block conforming to the schema, and we hand back the block's
``input`` serialised as JSON so the layer above parses it exactly as it parses
every other provider's output. That is a genuine constraint applied by the
server, so :meth:`enforces_schema` returns True — but the client still *probes*
it rather than trusting this, because a claim of enforcement is exactly the
kind of thing that should be measured.

## What this provider cannot report

``RuntimeInfo`` describes weights resident on a local GPU. For a hosted model
there is no such thing — not "unknown", but *not applicable*. It returns all
fields None so ``gpu_fraction`` and ``fully_resident`` come back None, and the
manifest records the reason rather than a passing value. The same applies to
the throughput canary: it exists to catch a local server that has silently
spilled to system RAM, and it cannot mean anything here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mironba.llm.providers.base import (
    ModelInfo,
    ProviderError,
    RawCompletion,
    RuntimeInfo,
    SamplingParams,
    get_json,
    post_json,
)

#: Pinned. The API is versioned by date and a silent server-side change would
#: be indistinguishable from a model change in an M10 comparison.
API_VERSION = "2023-06-01"

#: The tool the schema is smuggled through. Named rather than anonymous so the
#: forced ``tool_choice`` can refer to it.
SCHEMA_TOOL = "emit_result"

#: Models that reject a temperature parameter outright (HTTP 400,
#: "`temperature` is deprecated for this model"). Measured against the live API
#: on 2026-08-01, not assumed: Haiku 4.5 accepts it, Sonnet 5 and Opus 5 do not.
#:
#: The consequence is methodological, not cosmetic. Qwen's arms ran at
#: temperature 0.8; these models cannot be given a temperature at all, so those
#: comparisons are not sampling-matched and the manifest says so rather than
#: implying a match.
TEMPERATURE_DEPRECATED = ("claude-sonnet-5", "claude-opus-5")


def rejects_temperature(model: str) -> bool:
    return any(model.startswith(m) for m in TEMPERATURE_DEPRECATED)


#: Why the hardware fields are empty. Recorded in the manifest verbatim, so a
#: reader never sees a blank and guesses.
NOT_APPLICABLE = (
    "hosted API: no local weights, so GPU residency and throughput canary do "
    "not apply. Recorded as not-applicable rather than as passing - a canary "
    "that cannot fail is not a check."
)


#: Where the key lives. Project-local and gitignored, **not** a system
#: environment variable.
#:
#: Exporting ANTHROPIC_API_KEY globally is the trap: interactive Claude Code
#: picks it up and silently switches from the subscription to pay-as-you-go
#: billing. A file read by exactly one module cannot do that to a process that
#: never reads it.
KEY_FILE = Path(__file__).resolve().parents[3] / ".secrets" / "anthropic.key"


def read_api_key(path: Path | None = None) -> str:
    """The key, from the gitignored project file. No environment fallback.

    An environment fallback would defeat the point: the whole reason for the
    file is that a variable set for one purpose leaks into every other process
    started from the same shell.
    """
    path = path or KEY_FILE
    try:
        key = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise ProviderError(
            f"no API key at {path}. Create it with the key as its only "
            "contents; the directory is gitignored. This provider deliberately "
            "does not read ANTHROPIC_API_KEY from the environment - a variable "
            "exported for this would also flip interactive Claude Code to "
            "pay-as-you-go billing."
        ) from None
    if not key:
        raise ProviderError(f"{path} is empty.")
    return key


def _strip_meta(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits ``$defs``/``title``; the API accepts a plain JSON Schema.

    ``$defs`` and ``$ref`` are kept - nested models are legitimate - but the
    top-level ``title`` is dropped because it is decoration, not constraint.
    """
    out = {k: v for k, v in schema.items() if k != "title"}
    out.setdefault("type", "object")
    return out


class AnthropicProvider:
    name = "anthropic"

    def enforces_schema(self) -> bool:
        """A forced tool call is a real server-side constraint.

        Reported True, and probed anyway by ``llm/client.py`` - the difference
        between "we asked for enforcement" and "enforcement was observed" is
        the entire reason that probe exists.
        """
        return True

    def _headers(self) -> dict[str, str]:
        key = read_api_key()
        return {
            "x-api-key": key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def _url(self, base_url: str) -> str:
        root = (base_url or "https://api.anthropic.com").rstrip("/")
        return f"{root}/v1/messages"

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
        # The system prompt is a top-level field here, not a message with
        # role=system. Passing it as a message is accepted and then largely
        # ignored, which degrades instruction-following in a way that would
        # read as a model weakness.
        system = "\n\n".join(
            m["content"] for m in messages if m.get("role") == "system"
        )
        turns = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant")
        ]

        body: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": params.max_tokens,
        }
        if system:
            body["system"] = system
        # Anthropic rejects temperature and top_p together, so top_p is dropped.
        # And the newest models reject temperature outright - Sonnet 5 and
        # Opus 5 answer "`temperature` is deprecated for this model" with a 400.
        # Sending it anyway would fail every call; silently substituting a
        # default would put a sampling value in the manifest that was never set.
        # So it is omitted and recorded null, like seed and top_p.
        if not rejects_temperature(model):
            body["temperature"] = params.temperature

        if schema is not None:
            body["tools"] = [{
                "name": SCHEMA_TOOL,
                "description": "Return the result in the required structure.",
                "input_schema": _strip_meta(schema),
            }]
            body["tool_choice"] = {"type": "tool", "name": SCHEMA_TOOL}

        payload, latency = post_json(
            self._url(base_url), body, timeout=timeout, headers=self._headers()
        )

        text, thinking = "", ""
        for block in payload.get("content", []):
            kind = block.get("type")
            if kind == "tool_use":
                # The whole point: hand back the validated tool input as JSON so
                # every layer above is provider-agnostic.
                text = json.dumps(block.get("input", {}))
            elif kind == "text":
                text += block.get("text", "")
            elif kind == "thinking":
                thinking += block.get("thinking", "")

        usage = payload.get("usage", {}) or {}
        return RawCompletion(
            text=text,
            latency_s=latency,
            model=payload.get("model", model),
            # "max_tokens" is this API's spelling of "length"; RawCompletion
            # .truncated already knows both.
            finish_reason=payload.get("stop_reason"),
            thinking_text=thinking,
            usage={
                "prompt_tokens": usage.get("input_tokens"),
                "completion_tokens": usage.get("output_tokens"),
                "total_tokens": (usage.get("input_tokens") or 0)
                + (usage.get("output_tokens") or 0),
            },
            raw=payload,
        )

    def list_models(self, base_url: str, timeout: float = 10.0) -> list[ModelInfo]:
        """Ask the API what it serves.

        Anthropic publishes /v1/models, so this is a real query rather than a
        hardcoded list that would rot the moment a model is deprecated.
        """
        root = (base_url or "https://api.anthropic.com").rstrip("/")
        data = get_json(f"{root}/v1/models", headers=self._headers(), timeout=timeout)
        return [
            ModelInfo(
                model_id=entry.get("id", ""),
                quantization="not-applicable (hosted)",
                families=("anthropic",),
            )
            for entry in data.get("data", [])
        ]

    def model_info(self, base_url: str, model: str, timeout: float = 30) -> ModelInfo:
        """What is knowable about a hosted model: its id and the API version.

        Quantization is deliberately "not-applicable" rather than "unknown".
        Unknown invites someone to go and find it; there is nothing to find.
        """
        return ModelInfo(
            model_id=model,
            quantization="not-applicable (hosted)",
            context_length=None,
            families=("anthropic",),
        )

    def runtime_info(self, base_url: str, model: str, timeout: float = 30) -> RuntimeInfo:
        """All None, on purpose. See :data:`NOT_APPLICABLE`."""
        return RuntimeInfo(size_bytes=None, size_vram_bytes=None, context_length=None)
