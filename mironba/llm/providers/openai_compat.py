"""Anything speaking OpenAI's ``/v1/chat/completions``.

vLLM, SGLang, llama.cpp's server, LM Studio, OpenAI, DeepSeek, OpenRouter. The
charter's plan is that this is the common case and adapters are the exception.

Schema enforcement here is ``response_format: json_schema``, which vLLM and
SGLang implement with real guided decoding (xgrammar/outlines) and which the
hosted APIs implement server-side. Servers that accept the field and ignore it
exist; ``enforces_schema`` is honest that we cannot tell from here, and the
client records which defence was actually in play.
"""

from __future__ import annotations

import os
from typing import Any

from mironba.llm.providers.base import (
    ModelInfo,
    ProviderError,
    RawCompletion,
    SamplingParams,
    get_json,
    post_json,
)


class OpenAICompatibleProvider:
    name = "openai_compatible"

    #: An OpenAI-shaped endpoint may or may not honour the schema. Reporting
    #: True here would let a "0% schema failure" claim rest on an assumption.
    def enforces_schema(self) -> bool:
        return False

    def _root(self, base_url: str) -> str:
        root = base_url.rstrip("/")
        return root if root.endswith("/v1") else f"{root}/v1"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        return {"Authorization": f"Bearer {key}"} if key else {}

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
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": params.temperature,
            "top_p": params.top_p,
            "max_tokens": params.max_tokens,
            "stream": False,
        }
        if params.seed is not None:
            body["seed"] = params.seed
        if params.stop:
            body["stop"] = list(params.stop)
        if schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.get("title", "response"),
                    "schema": schema,
                    "strict": True,
                },
            }

        data, latency = post_json(
            f"{self._root(base_url)}/chat/completions",
            body,
            timeout=timeout,
            headers=self._headers(),
        )

        choices = data.get("choices")
        if not choices:
            raise ProviderError(
                f"no choices in response. Keys: {sorted(data)}. "
                f"Error: {data.get('error')!r}"
            )
        message = choices[0].get("message", {}) or {}

        return RawCompletion(
            text=message.get("content", "") or "",
            latency_s=latency,
            model=data.get("model", model),
            finish_reason=choices[0].get("finish_reason"),
            thinking_text=message.get("reasoning_content", "") or "",
            usage=data.get("usage", {}) or {},
            raw={k: v for k, v in data.items() if k != "choices"},
        )

    def list_models(self, base_url: str, timeout: float = 10.0) -> list[ModelInfo]:
        data = get_json(f"{self._root(base_url)}/models", timeout=timeout)
        return [
            ModelInfo(model_id=entry.get("id", ""))
            for entry in data.get("data", [])
        ]
