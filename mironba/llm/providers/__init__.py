"""Provider adapters. The only place a server's name may appear.

A provider's whole job is to answer one question — *how does this server accept
a JSON schema?* — because that is the only thing that meaningfully differs
between them for our purposes. Sampling knobs differ in spelling; schema
enforcement differs in kind, and it is the difference that decides whether a
3B-active model can be trusted to fill a form.
"""

from __future__ import annotations

from mironba.llm.providers.base import (
    Provider,
    ProviderError,
    RawCompletion,
    SamplingParams,
)
from mironba.llm.providers.ollama import OllamaProvider
from mironba.llm.providers.openai_compat import OpenAICompatibleProvider

#: Registry keyed by the ``server:`` field in configs/models.yaml.
PROVIDERS: dict[str, type[Provider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatibleProvider,
    "vllm": OpenAICompatibleProvider,
    "sglang": OpenAICompatibleProvider,
    "llamacpp": OpenAICompatibleProvider,
    "lmstudio": OpenAICompatibleProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def provider_for(server: str) -> Provider:
    try:
        return PROVIDERS[server]()
    except KeyError:
        raise ProviderError(
            f"unknown server {server!r}. Known: {', '.join(sorted(PROVIDERS))}. "
            "Anything speaking OpenAI-compatible /v1/chat/completions can use "
            "server: openai_compatible."
        ) from None


__all__ = [
    "PROVIDERS",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderError",
    "RawCompletion",
    "SamplingParams",
    "provider_for",
]
