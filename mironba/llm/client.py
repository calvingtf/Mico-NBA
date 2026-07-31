"""The single interface the rest of the codebase sees.

``complete(messages, schema, profile)`` per the charter. Roles map to profiles
in ``configs/models.yaml``, so a laptop running Ollama and a box running vLLM
execute the same agent code.

Structured output is the failure point for small local models, and the defences
run in the charter's order:

1. **Constrain decoding at the server.** The pydantic schema is passed to the
   provider, which hands it to the server's structured-output parameter. Always
   sent — but see the caveat below, because sending it is not the same as it
   being honoured.
1b. **Put the schema in the prompt too**, whenever server enforcement is not
   *verified*. The charter says not to rely on prompt instructions alone; it
   does not say to withhold them. A model that has never seen the schema and is
   not constrained by one is guessing field names, which is precisely what the
   first live run produced.
2. **Validate on return.** Pydantic parse. On failure, exactly one repair retry
   with the validation error fed back verbatim. Then raise.
3. **Keep schemas small.** Not enforceable here; it is a call-site discipline,
   and ``agents/gm.py`` two-steps its action selection because of it.

The caveat: ``Provider.enforces_schema()`` means "we have verified this server
constrains decoding", and it is currently False everywhere. Ollama 0.31.1 was
observed accepting ``format`` and ignoring it. Claiming enforcement we have not
verified would make a measured failure rate describe a defence that never ran.

Every completion, including every failure, is written to disk under the run id.
A schema-failure rate nobody can audit is a number, not a finding.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from mironba.llm.providers import ProviderError, SamplingParams, provider_for
from mironba.llm.providers.base import ModelInfo, RawCompletion, RuntimeInfo
from mironba.world.manifest import ManifestError, Run

T = TypeVar("T", bound=BaseModel)

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "models.yaml"
RAW_LOG = "llm_calls.jsonl"

#: Bumped when the agent-facing schemas change shape. Recorded in the manifest
#: so M5 never compares a run against one that was answering a different form.
SCHEMA_VERSION = 1


class LLMError(RuntimeError):
    """Base for failures that are ours, not the server's."""


class SchemaFailure(LLMError):
    """The model could not fill the form, twice.

    Carries both attempts so the failure is inspectable rather than merely
    counted. This is the exception the charter means by "then fail loudly".
    """

    def __init__(self, schema_name: str, attempts: list[dict[str, Any]]) -> None:
        self.schema_name = schema_name
        self.attempts = attempts
        errors = "; ".join(a.get("error", "") for a in attempts)
        super().__init__(
            f"{schema_name}: model failed schema validation on "
            f"{len(attempts)} attempts (initial + repair). Errors: {errors}"
        )


@dataclass
class ProfileConfig:
    name: str
    base_url: str
    model: str
    server: str = "ollama"
    temperature: float = 0.8
    top_p: float = 0.95
    seed: int | None = None
    thinking: bool = False
    max_tokens: int = 1024
    context_length: int | None = None
    gpu_layers: int | None = None
    request_timeout_s: float = 300.0

    def sampling(self) -> SamplingParams:
        return SamplingParams(
            temperature=self.temperature,
            top_p=self.top_p,
            seed=self.seed,
            thinking=self.thinking,
            max_tokens=self.max_tokens,
            context_length=self.context_length,
            gpu_layers=self.gpu_layers,
        )


@dataclass
class CallStats:
    """What actually happened, for the README numbers."""

    calls: int = 0
    first_attempt_failures: int = 0
    repairs_attempted: int = 0
    repairs_succeeded: int = 0
    gave_up: int = 0
    truncations: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def schema_failure_rate(self) -> float:
        """Share of calls whose *first* attempt failed validation.

        First attempt, not final. The repair retry is our mitigation; folding
        it in would measure the mitigation and report it as the model.
        """
        return self.first_attempt_failures / self.calls if self.calls else 0.0

    @property
    def unrecovered_rate(self) -> float:
        return self.gave_up / self.calls if self.calls else 0.0

    @property
    def mean_latency_s(self) -> float:
        return statistics.fmean(self.latencies) if self.latencies else 0.0

    @property
    def median_latency_s(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "first_attempt_failures": self.first_attempt_failures,
            "schema_failure_rate": round(self.schema_failure_rate, 4),
            "repairs_attempted": self.repairs_attempted,
            "repairs_succeeded": self.repairs_succeeded,
            "gave_up": self.gave_up,
            "unrecovered_rate": round(self.unrecovered_rate, 4),
            "truncations": self.truncations,
            "mean_latency_s": round(self.mean_latency_s, 2),
            "median_latency_s": round(self.median_latency_s, 2),
        }


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def render_field_list(json_schema: dict[str, Any]) -> str:
    """A compact field list for the prompt — not the JSON Schema document.

    Pasting the raw schema in was the obvious first attempt and it backfired
    immediately: qwen3.6 echoed the *schema* back as its answer, keys and all
    (``{"description": "Step one...", "required": [...]}``). A schema document
    looks exactly like a JSON object to a model being asked for a JSON object.

    So this renders what the model actually needs — field names, types, allowed
    values — plus a skeleton to imitate. Small models copy examples far more
    reliably than they interpret specifications.
    """
    lines: list[str] = ["Reply with a single JSON object with exactly these fields:"]
    skeleton: dict[str, Any] = {}
    for name, spec in (json_schema.get("properties") or {}).items():
        if spec.get("enum"):
            allowed = " | ".join(json.dumps(v) for v in spec["enum"])
            lines.append(f'  "{name}": one of {allowed}')
            skeleton[name] = spec["enum"][0]
        elif spec.get("type") == "array":
            lines.append(f'  "{name}": array of strings')
            skeleton[name] = ["..."]
        else:
            lines.append(f'  "{name}": string')
            skeleton[name] = "..."
    lines.append("")
    lines.append("Shape to follow: " + json.dumps(skeleton))
    lines.append(
        "Output only that JSON object. Do not repeat this description, do not "
        "wrap it in a code fence, and do not add fields."
    )
    return "\n".join(lines)


def resolve_profile(config: dict[str, Any], name: str) -> ProfileConfig:
    """Resolve a role or profile name to a concrete profile.

    Roles are looked up first, so call sites name a *job* ("gm_agent") rather
    than a model, which is what makes the swap in M5 a config edit.

    Module-level rather than a method because the manifest needs the resolved
    model *before* a client can exist — a client requires a Run, and a Run
    requires a manifest that already names the model. Resolution has no
    business depending on any of that.
    """
    roles = config.get("roles", {}) or {}
    profiles = config.get("profiles", {}) or {}
    resolved = roles.get(name, name)
    if resolved not in profiles:
        raise LLMError(
            f"no profile {resolved!r} (from {name!r}). "
            f"Profiles: {', '.join(sorted(profiles))}. "
            f"Roles: {', '.join(sorted(roles))}."
        )
    merged = {**dict(config.get("defaults", {}) or {}), **dict(profiles[resolved])}
    known = ProfileConfig.__dataclass_fields__
    profile = ProfileConfig(
        name=resolved, **{k: v for k, v in merged.items() if k in known}
    )
    _refuse_unreproducible_transport(profile)
    return profile


def _refuse_unreproducible_transport(profile: ProfileConfig) -> None:
    """Bar measurement code from a transport that cannot set a seed.

    Enforced here rather than at the call sites because this is the one
    function every path goes through. A convention documented in a provider
    module gets broken by whoever did not read it, and the failure would be
    silent: a run that looks like every other run and cannot be reproduced.

    The caller is identified by walking the stack, which is blunt but honest -
    it catches indirect routes (eval imports a sim helper that builds a client)
    that an explicit argument would not.
    """
    if profile.server != "claude_sdk":
        return
    import inspect

    from mironba.llm.providers.claude_sdk import assert_not_measurement_profile

    for frame in inspect.stack()[1:]:
        module = frame.frame.f_globals.get("__name__", "")
        assert_not_measurement_profile(module, profile.name)


def probe_model(cfg: ProfileConfig) -> ModelInfo:
    """Ask the server what it knows about this model.

    Quantization is read from the server rather than written by hand in the
    config, because a hand-copied label stays Q4_K_M in the manifest long after
    someone pulled the Q8 build — and M5 would then compare two quantizations
    while reporting one.
    """
    provider = provider_for(cfg.server)
    try:
        for info in provider.list_models(cfg.base_url):
            if info.model_id == cfg.model:
                return info
    except ProviderError:
        pass
    return ModelInfo(model_id=cfg.model)


def probe_runtime(cfg: ProfileConfig, *, warm: bool = True) -> RuntimeInfo:
    """How much of the model is on the GPU, warming it first if needed.

    Ollama lists nothing under /api/ps until a model is loaded, so probing
    before the first real call would always record "unknown" — and the manifest
    is written before the first real call by design. The fix is to load the
    model deliberately with a one-token request, then probe, then mint the
    manifest. On an already-warm server the warm-up returns immediately.

    A failure here is never fatal: an unknown offload split is recorded as
    unknown. Refusing to run because we could not measure residency would be a
    worse trade than running with the gap declared.
    """
    provider = provider_for(cfg.server)
    info = provider.runtime_info(cfg.base_url, cfg.model)
    if info.size_bytes or not warm:
        return info
    try:
        provider.chat(
            base_url=cfg.base_url,
            model=cfg.model,
            messages=[{"role": "user", "content": "ok"}],
            schema=None,
            params=SamplingParams(temperature=0.0, max_tokens=1),
            timeout=cfg.request_timeout_s,
        )
    except ProviderError:
        return RuntimeInfo()
    return provider.runtime_info(cfg.base_url, cfg.model)


def preflight(cfg: ProfileConfig) -> list[str]:
    """Check the configured model is actually servable. Returns problems.

    A missing model otherwise surfaces as a confusing HTTP error deep inside a
    run, after the manifest claims it was used.
    """
    provider = provider_for(cfg.server)
    try:
        available = provider.list_models(cfg.base_url)
    except ProviderError as exc:
        return [f"{cfg.server} at {cfg.base_url} unreachable: {exc}"]
    names = {m.model_id for m in available}
    if cfg.model in names:
        return []
    hint = f" Server has: {', '.join(sorted(names)) or '(none)'}."
    if cfg.model.split(":")[0] in {n.split(":")[0] for n in names}:
        hint += " A different tag of the same model is present."
    return [f"model {cfg.model!r} not available on {cfg.server}.{hint}"]


def _strip_code_fence(text: str) -> str:
    """Remove a ```json fence if the model wrapped its object in one.

    Not a repair of the model's *content* — only of a wrapper that some servers
    add regardless of schema enforcement. Anything beyond this is guessing at
    intent, which is how a validator starts approving things it should not.
    """
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    return fence.group(1) if fence else stripped


class LLMClient:
    """Model-agnostic completion with schema enforcement and one repair.

    Requires a ``Run``. There is no unmanifested mode: a completion that cannot
    name the model, sampling parameters and code revision behind it is not
    evidence of anything, and M5 exists to compare exactly those.
    """

    def __init__(
        self,
        run: Run,
        config: dict[str, Any] | None = None,
        config_path: Path | str = DEFAULT_CONFIG,
        schema_enforcement: bool | None = None,
    ) -> None:
        if not isinstance(run, Run):
            raise ManifestError(
                f"LLMClient requires a Run, got {type(run).__name__}. Every raw "
                "completion is logged under a run id, including failures."
            )
        self.run = run
        self.config = config if config is not None else load_config(config_path)
        self.stats = CallStats()
        self._seq = 0
        #: Whether the server was *observed* to constrain decoding, measured by
        #: llm/probe.py before the run. None means it could not be measured.
        #: Never inferred from the fact that we sent a schema — that is exactly
        #: the mistake M1 made, and it made the failure rate describe a defence
        #: that was not running.
        self.schema_enforcement = schema_enforcement

    # -- configuration ----------------------------------------------------

    def profile_for(self, name: str) -> ProfileConfig:
        return resolve_profile(self.config, name)

    def preflight(self, profile: str = "default") -> list[str]:
        return preflight(self.profile_for(profile))

    def model_info(self, profile: str = "default") -> ModelInfo:
        return probe_model(self.profile_for(profile))

    # -- completion -------------------------------------------------------

    def complete(
        self,
        messages: list[dict[str, str]],
        schema: type[T] | None = None,
        profile: str = "default",
        *,
        purpose: str = "",
    ) -> T | str:
        """One completion, validated against ``schema`` if given.

        Returns a populated pydantic model, or raw text when no schema is
        supplied. Raises ``SchemaFailure`` after one failed repair — never
        returns a partially-understood object, and never invents a default for
        a field the model did not fill.
        """
        cfg = self.profile_for(profile)
        provider = provider_for(cfg.server)
        json_schema = schema.model_json_schema() if schema is not None else None
        schema_name = schema.__name__ if schema is not None else "text"
        self._seq += 1
        seq = self._seq
        self.stats.calls += 1

        attempts: list[dict[str, Any]] = []
        conversation = list(messages)

        # Defence 1b: when the server's enforcement is unverified, the schema
        # also goes in the prompt. The charter says not to *rely* on prompt
        # instructions, not to withhold them — and with enforcement silently
        # absent, a model that has never seen the schema is guessing field
        # names. That is what produced `{"trade": {"sent_ids": [...]}}` on the
        # first live run: a reasonable shape, and not the one we asked for.
        schema_in_prompt = json_schema is not None and self.schema_enforcement is not True
        if schema_in_prompt:
            conversation = self._with_schema_in_prompt(conversation, json_schema)

        for attempt in range(2):  # initial + one repair
            completion = provider.chat(
                base_url=cfg.base_url,
                model=cfg.model,
                messages=conversation,
                schema=json_schema,
                params=cfg.sampling(),
                timeout=cfg.request_timeout_s,
            )
            self.stats.latencies.append(completion.latency_s)
            if completion.truncated:
                self.stats.truncations += 1

            if schema is None:
                self._log(
                    seq, attempt, cfg, provider, schema_name, purpose,
                    conversation, completion, ok=True, error=None,
                    schema_in_prompt=schema_in_prompt,
                )
                return completion.text

            try:
                parsed = schema.model_validate_json(_strip_code_fence(completion.text))
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:1500]
                attempts.append({"attempt": attempt, "error": error,
                                 "text": completion.text[:2000]})
                self._log(
                    seq, attempt, cfg, provider, schema_name, purpose,
                    conversation, completion, ok=False, error=error,
                    schema_in_prompt=schema_in_prompt,
                )
                if attempt == 0:
                    self.stats.first_attempt_failures += 1
                    self.stats.repairs_attempted += 1
                    conversation = self._repair_conversation(
                        messages, completion.text, error, json_schema
                    )
                    continue
                self.stats.gave_up += 1
                raise SchemaFailure(schema_name, attempts) from exc

            self._log(
                seq, attempt, cfg, provider, schema_name, purpose,
                conversation, completion, ok=True, error=None,
                    schema_in_prompt=schema_in_prompt,
            )
            if attempt == 1:
                self.stats.repairs_succeeded += 1
            return parsed

        raise AssertionError("unreachable")

    def _with_schema_in_prompt(
        self, messages: list[dict[str, str]], json_schema: dict
    ) -> list[dict[str, str]]:
        """Append the schema to the final user turn.

        Appended rather than made a separate message so it travels with the
        instruction it constrains — some chat templates treat a trailing
        system-ish turn as lower priority than the last user turn.
        """
        block = render_field_list(json_schema)
        out = list(messages)
        for i in range(len(out) - 1, -1, -1):
            if out[i]["role"] == "user":
                out[i] = {**out[i], "content": out[i]["content"] + "\n\n" + block}
                return out
        return [*out, {"role": "user", "content": block}]

    def _repair_conversation(
        self,
        original: list[dict[str, str]],
        bad_output: str,
        error: str,
        json_schema: dict | None,
    ) -> list[dict[str, str]]:
        """Feed the validation error back, verbatim.

        Verbatim matters. A summarised error ("invalid format") gives the model
        nothing to act on; pydantic's message names the field and what was
        wrong with it, which is precisely the information needed to fix it.
        """
        return [
            *original,
            {"role": "assistant", "content": bad_output},
            {
                "role": "user",
                "content": (
                    "Your previous reply failed schema validation.\n\n"
                    f"Validation error:\n{error}\n\n"
                    f"Required JSON schema:\n{json.dumps(json_schema, indent=2)}\n\n"
                    "Reply with corrected JSON only. No prose, no code fence."
                ),
            },
        ]

    def _log(
        self,
        seq: int,
        attempt: int,
        cfg: ProfileConfig,
        provider: Any,
        schema_name: str,
        purpose: str,
        conversation: list[dict[str, str]],
        completion: RawCompletion,
        *,
        ok: bool,
        error: str | None,
        schema_in_prompt: bool = False,
    ) -> None:
        """Every completion to disk, successes and failures alike."""
        self.run.append_jsonl(
            RAW_LOG,
            {
                "seq": seq,
                "attempt": attempt,
                "purpose": purpose,
                "schema": schema_name,
                "profile": cfg.name,
                "model": completion.model,
                "server": cfg.server,
                "schema_sent_to_server": schema_name != "text",
                # Observed, not requested. None = not measured this run.
                "schema_enforcement_observed": self.schema_enforcement,
                "schema_in_prompt": schema_in_prompt,
                "temperature": cfg.temperature,
                "top_p": cfg.top_p,
                "seed": cfg.seed,
                "thinking": cfg.thinking,
                "latency_s": round(completion.latency_s, 3),
                "finish_reason": completion.finish_reason,
                "truncated": completion.truncated,
                "usage": completion.usage,
                "ok": ok,
                "validation_error": error,
                "messages": conversation,
                "response_text": completion.text,
                "thinking_text": completion.thinking_text[:4000],
            },
        )
