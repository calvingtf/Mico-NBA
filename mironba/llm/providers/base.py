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
    #: How many transformer layers to place on the GPU. None lets the server
    #: decide, which is usually right and was observably wrong here: with
    #: 22.9 GiB free the scheduler put a 21.5 GiB model entirely on the CPU and
    #: stayed there. Generic name; each provider translates it, or ignores it.
    gpu_layers: int | None = None


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
class RuntimeInfo:
    """How the model is actually resident right now.

    A latency number is meaningless without this. The same model at the same
    quantization ran roughly six times slower in one M1 batch than another
    because part of it had spilled to system RAM, and nothing in the manifest
    said so — which would have made an M5 latency comparison silently invalid.

    ``size_vram`` of zero with a non-zero ``size`` means pure CPU inference.
    Both unknown (None) is honest for a server that does not publish it.
    """

    size_bytes: int | None = None
    size_vram_bytes: int | None = None
    #: The context the server actually allocated, which is NOT the model's
    #: maximum. The manifest recorded the maximum (262144 for this model) and
    #: would not have noticed a server started with a different context — the
    #: two differ by more than an order of magnitude and only one of them
    #: describes the run.
    context_length: int | None = None

    @property
    def gpu_fraction(self) -> float | None:
        if not self.size_bytes:
            return None
        return round((self.size_vram_bytes or 0) / self.size_bytes, 4)

    @property
    def fully_resident(self) -> bool | None:
        """Whether the whole model sits in VRAM.

        Derived, not reported: servers disagree on how to say it, and a
        threshold in one place beats the same arithmetic at every call site.
        A 1% tolerance covers rounding between the two figures.
        """
        fraction = self.gpu_fraction
        return None if fraction is None else fraction >= 0.99


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

    def runtime_info(self, base_url: str, model: str, timeout: float = 10.0) -> RuntimeInfo:
        """What is loaded right now, and how much of it is on the GPU.

        Returns an empty RuntimeInfo when the server does not publish it or the
        model is not loaded. Never guesses.
        """
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


def get_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GET and parse JSON. ``headers`` because some APIs authenticate reads."""
    request = urllib.request.Request(url, method="GET", headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"{url} -> {type(exc).__name__}: {exc}") from exc
