"""Ollama, over its native ``/api/chat``.

Deliberately not Ollama's OpenAI-compatibility shim. The native endpoint takes
a JSON schema in ``format`` and constrains decoding against it, which is the
charter's first line of defence against a small model drifting off-schema. The
compatibility layer's ``response_format`` support has varied by version, and a
silently-ignored schema is the worst outcome available: it looks like the
defence is on while the model free-associates.

This file is allowed to know the word "ollama". Nothing outside
``llm/providers/`` is.
"""

from __future__ import annotations

from typing import Any

from mironba.llm.providers.base import (
    ModelInfo,
    RuntimeInfo,
    ProviderError,
    RawCompletion,
    SamplingParams,
    get_json,
    post_json,
)


class OllamaProvider:
    name = "ollama"

    def enforces_schema(self) -> bool:
        """Whether *any* Ollama may be assumed to constrain decoding.

        False, and it stays False even though the server on this machine now
        demonstrably does enforce. The two observations, both measured with
        ``llm/probe.py``:

          * 0.31.1, qwen3.6:35b-a3b — 0/9 conformed. ``format`` carrying the
            full schema returned ``{"trade": {"sent_ids": ...}}``; the legacy
            ``format: "json"`` returned free prose. A grammar produces neither.
          * 0.32.5, both qwen3.6:27b and qwen3.6:35b-a3b — 9/9 conformed, on
            flat, ``$defs``-bearing and inlined shapes alike. Same probe, same
            models, upgraded server. An unparseable schema is now a hard HTTP
            400 rather than a silent no-op, which is the honest behaviour.

        So the capability is real but version-dependent, and this method cannot
        see a version. Returning True would restore exactly the M1 defect: a
        static claim standing in for a fact about the process actually running.
        The client takes its flag from ``observed_enforcement()`` instead, which
        measures it per (server, base_url, model) and can be wrong only about
        the run it was measured in.
        """
        return False

    def _root(self, base_url: str) -> str:
        """Accept the OpenAI-style base_url people paste from the charter.

        configs/models.yaml carries ``http://localhost:11434/v1`` because that
        is what every other server wants. The native API lives one level up.
        """
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        return root

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
        options: dict[str, Any] = {
            "temperature": params.temperature,
            "top_p": params.top_p,
            "num_predict": params.max_tokens,
        }
        if params.seed is not None:
            options["seed"] = params.seed
        if params.context_length:
            options["num_ctx"] = params.context_length
        if params.gpu_layers is not None:
            options["num_gpu"] = params.gpu_layers
        if params.stop:
            options["stop"] = list(params.stop)

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": params.thinking,
            "options": options,
        }
        if schema is not None:
            # The whole point of this provider. Not an instruction — a grammar.
            body["format"] = schema

        data, latency = post_json(
            f"{self._root(base_url)}/api/chat", body, timeout=timeout
        )

        message = data.get("message")
        if not isinstance(message, dict):
            raise ProviderError(
                f"ollama returned no message object. Body keys: "
                f"{sorted(data)}. Error field: {data.get('error')!r}"
            )

        return RawCompletion(
            text=message.get("content", "") or "",
            latency_s=latency,
            model=data.get("model", model),
            finish_reason=data.get("done_reason"),
            thinking_text=message.get("thinking", "") or "",
            usage={
                "prompt_tokens": data.get("prompt_eval_count"),
                "completion_tokens": data.get("eval_count"),
                "total_duration_ns": data.get("total_duration"),
                "load_duration_ns": data.get("load_duration"),
            },
            raw={k: v for k, v in data.items() if k != "message"},
        )

    def list_models(self, base_url: str, timeout: float = 10.0) -> list[ModelInfo]:
        data = get_json(f"{self._root(base_url)}/api/tags", timeout=timeout)
        models = []
        for entry in data.get("models", []):
            details = entry.get("details", {}) or {}
            models.append(
                ModelInfo(
                    model_id=entry.get("name", ""),
                    quantization=details.get("quantization_level", "unknown"),
                    context_length=details.get("context_length"),
                    families=tuple(details.get("families") or ()),
                )
            )
        return models

    def runtime_info(
        self, base_url: str, model: str, timeout: float = 10.0
    ) -> RuntimeInfo:
        """Read the offload split from /api/ps.

        Only meaningful once the model is loaded — Ollama lists nothing until
        then, which is why the caller warms it before minting the manifest.
        """
        try:
            data = get_json(f"{self._root(base_url)}/api/ps", timeout=timeout)
        except ProviderError:
            return RuntimeInfo()
        for entry in data.get("models", []):
            if entry.get("name") == model or entry.get("model") == model:
                return RuntimeInfo(
                    size_bytes=entry.get("size"),
                    size_vram_bytes=entry.get("size_vram"),
                )
        return RuntimeInfo()
