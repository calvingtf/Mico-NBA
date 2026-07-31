"""Observe whether a server actually constrains decoding.

M1 logged ``schema_enforced_by_server: true`` because the code *sent* a schema.
Ollama accepted it and ignored it, so the flag recorded an intention rather than
a fact, and the resulting failure rate described a defence that never ran. This
module replaces the claim with a measurement.

The probe is built to be unambiguous. It asks a question whose natural answer is
prose, supplies the schema **only** through the server's structured-output
parameter, and never mentions it in the prompt. Then:

  * output parses against the schema  -> the grammar is doing something
  * output is prose                   -> the parameter was accepted and ignored

Asking a question the model would answer correctly anyway would prove nothing,
which is the trap: a probe like "return {"n": 5}" passes under both conditions
and would have reported enforcement in M1 too.

The ``$defs`` question is tested rather than assumed. Pydantic emits
``$ref``/``$defs`` for enum fields, and grammar compilers vary in how well they
follow references — a plausible explanation for silent non-enforcement. So the
probe runs the same shape twice, once with references and once inlined, and
reports both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from mironba.llm.providers import ProviderError, SamplingParams, provider_for

#: A question whose natural, unconstrained answer is prose. If the server is
#: enforcing anything at all, the reply cannot be a paragraph.
PROBE_PROMPT = "Explain in a few sentences why the sky appears blue."

#: Flat, no references.
FLAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "ProbeFlat",
    "properties": {
        "wavelength_nm": {"type": "integer"},
        "verdict": {"type": "string", "enum": ["scattering", "absorption"]},
    },
    "required": ["wavelength_nm", "verdict"],
    "additionalProperties": False,
}

#: The same shape, expressed through $defs/$ref as pydantic would emit it.
REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "ProbeRef",
    "$defs": {
        "Cause": {"title": "Cause", "enum": ["scattering", "absorption"], "type": "string"}
    },
    "properties": {
        "wavelength_nm": {"type": "integer"},
        "verdict": {"$ref": "#/$defs/Cause"},
    },
    "required": ["wavelength_nm", "verdict"],
    "additionalProperties": False,
}


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Flatten ``$ref``/``$defs`` into a self-contained schema.

    Offered because a schema a grammar compiler cannot resolve may be dropped
    silently, and "silently dropped" is indistinguishable from "not supported"
    at the call site. Inlining removes that variable.

    Non-recursive schemas only, which covers everything in ``llm/schemas.py``.
    A recursive one would need a cycle check, and we do not have one because the
    charter forbids nesting in agent-facing forms anyway.
    """
    defs = schema.get("$defs") or {}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                name = ref.rsplit("/", 1)[-1]
                target = defs.get(name)
                if target is None:
                    return {k: v for k, v in node.items() if k != "$ref"}
                merged = {**resolve(target)}
                merged.update({k: resolve(v) for k, v in node.items() if k != "$ref"})
                return merged
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)


@dataclass
class ProbeOutcome:
    """What one schema shape did over ``trials`` attempts."""

    label: str
    trials: int = 0
    conformed: int = 0
    prose: int = 0
    errors: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def enforced(self) -> bool:
        """Enforcement is all-or-nothing, deliberately.

        A grammar that lets one reply through in five is not a grammar. Treating
        4/5 as "mostly enforced" is how a defence gets credited for work the
        repair retry is actually doing.
        """
        return self.trials > 0 and self.conformed == self.trials

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "trials": self.trials,
            "conformed": self.conformed,
            "prose": self.prose,
            "errors": self.errors,
            "enforced": self.enforced,
            "samples": self.samples[:2],
        }


def _conforms(text: str, schema: dict[str, Any]) -> bool:
    try:
        parsed = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(parsed, dict):
        return False
    required = schema.get("required", [])
    if any(key not in parsed for key in required):
        return False
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False and set(parsed) - set(props):
        return False
    for key, spec in props.items():
        if key not in parsed:
            continue
        allowed = spec.get("enum")
        if allowed is not None and parsed[key] not in allowed:
            return False
        if spec.get("type") == "integer" and not isinstance(parsed[key], int):
            return False
    return True


def probe_shape(
    cfg,
    schema: dict[str, Any] | None,
    label: str,
    *,
    trials: int = 3,
) -> ProbeOutcome:
    provider = provider_for(cfg.server)
    outcome = ProbeOutcome(label=label)
    effective = schema if schema is None else dict(schema)
    for i in range(trials):
        try:
            completion = provider.chat(
                base_url=cfg.base_url,
                model=cfg.model,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                schema=effective,
                params=SamplingParams(
                    temperature=0.0,
                    seed=1000 + i,
                    max_tokens=200,
                    context_length=cfg.context_length,
                ),
                timeout=cfg.request_timeout_s,
            )
        except ProviderError:
            outcome.errors += 1
            continue
        outcome.trials += 1
        text = completion.text.strip()
        if schema is not None and _conforms(text, schema):
            outcome.conformed += 1
        else:
            outcome.prose += 1
            outcome.samples.append(text[:160])
    return outcome


def probe_schema_enforcement(cfg, *, trials: int = 3) -> dict[str, Any]:
    """Run every shape and report which, if any, the server enforces."""
    flat = probe_shape(cfg, FLAT_SCHEMA, "flat", trials=trials)
    ref = probe_shape(cfg, REF_SCHEMA, "with_$defs", trials=trials)
    inlined = probe_shape(cfg, inline_refs(REF_SCHEMA), "inlined_from_$defs", trials=trials)
    return {
        "server": cfg.server,
        "model": cfg.model,
        "enforced": flat.enforced,
        "defs_are_the_problem": inlined.enforced and not ref.enforced,
        "shapes": [flat.to_dict(), ref.to_dict(), inlined.to_dict()],
    }


#: One probe per (server, model) per process. A bench of twenty trials should
#: not pay for twenty probes, and the answer cannot change mid-run.
_CACHE: dict[tuple[str, str, str], bool] = {}


def observed_enforcement(cfg, *, trials: int = 2) -> bool | None:
    """Cached observation for the log flag. None when it could not be measured.

    None rather than False on error, because "the server was unreachable" and
    "the server ignored the schema" are different facts and only one of them is
    about the schema.
    """
    key = (cfg.server, cfg.base_url, cfg.model)
    if key in _CACHE:
        return _CACHE[key]
    outcome = probe_shape(cfg, FLAT_SCHEMA, "flat", trials=trials)
    if outcome.trials == 0:
        return None
    _CACHE[key] = outcome.enforced
    return outcome.enforced
