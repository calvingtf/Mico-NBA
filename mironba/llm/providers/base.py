"""The provider contract, and the HTTP plumbing every provider shares.

stdlib ``urllib`` only. The ingest already fetches over urllib and adding a
dependency to send one POST would be paying a maintenance cost for typing
convenience.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    """A server could not be reached or answered incomprehensibly.

    Never swallowed and never retried silently. A model that is not there is a
    different problem from a model that answered badly, and conflating them
    produces a schema-failure rate that is really an uptime statistic.
    """


@dataclass(frozen=True, slots=True)
class SamplingParams:
    temperature: float = 0.8
    top_p: float = 0.95
    seed: int | None = None
    thinking: bool = False
    max_tokens: int = 1024
    context_length: int | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RawCompletion:
    """What a server returned, before anyone tried to believe it."""

    text: str
    latency_s: float
    model: str
    finish_reason: str | None = None
    thinking_text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """Ran out of tokens mid-JSON.

        Worth separating from a schema failure: the model may have been filling
        the form correctly and simply been cut off, which is a max_tokens bug
        on our side, not a capability finding about the model.
        """
        return self.finish_reason in {"length", "max_tokens"}


@dataclass(frozen=True, slots=True)
class ModelInfo:
    model_id: str
    quantization: str = "unknown"
    context_length: int | None = None
    families: tuple[str, ...] = ()


class Provider(Protocol):
    """What the client needs from a server. Nothing more."""

    name: str

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
        """One completion. ``schema`` constrains decoding when supported."""
        ...

    def list_models(self, base_url: str, timeout: float = 10.0) -> list[ModelInfo]:
        """Models the server can serve now, for preflight."""
        ...

    def enforces_schema(self) -> bool:
        """Whether ``schema`` actually constrains decoding on this server.

        False means the schema degrades to a prompt instruction, and the
        measured failure rate is then a statement about the model's instruction
        following rather than about grammar-constrained decoding. The two get
        confused constantly; recording which one you measured is the fix.
        """
        ...


def post_json(
    url: str,
    body: dict[str, Any],
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], float]:
    """POST JSON, return ``(parsed, latency_seconds)``."""
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise ProviderError(f"{url} -> HTTP {exc.code} {exc.reason}: {detail}") from exc
    except TimeoutError as exc:
        raise ProviderError(
            f"{url} -> timed out after {timeout}s. A cold model load can exceed "
            "this; raise request_timeout_s in configs/models.yaml or warm the "
            "model first."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - surface the cause verbatim
        raise ProviderError(f"{url} -> {type(exc).__name__}: {exc}") from exc
    latency = time.monotonic() - started

    try:
        return json.loads(raw.decode("utf-8", "replace")), latency
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"{url} returned non-JSON: {raw[:300]!r}"
        ) from exc


def get_json(url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"{url} -> {type(exc).__name__}: {exc}") from exc
