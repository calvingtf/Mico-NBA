"""The LLM layer, offline.

No network. A fake provider stands in for the server so the schema-repair
path, the give-up path and the logging can be tested deterministically —
measuring those against a live model would make the suite slow and the
assertions probabilistic.

The live measurement is a separate, explicit job: `python -m mironba.sim.bench`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from mironba.llm import client as client_module
from mironba.llm.client import (
    LLMClient,
    LLMError,
    SchemaFailure,
    _strip_code_fence,
    resolve_profile,
)
from mironba.llm.providers import PROVIDERS, provider_for
from mironba.llm.providers.base import ProviderError, RawCompletion, SamplingParams
from mironba.llm.schemas import (
    AGENT_SCHEMAS,
    FORBIDDEN_FIELD_TOKENS,
    ActionChoice,
    TradeIntent,
)
from mironba.world.manifest import ManifestError, Run, build_manifest

CONFIG = {
    "defaults": {"server": "fake", "base_url": "http://x", "max_tokens": 64},
    "profiles": {
        "fast": {"model": "m-fast", "temperature": 0.8, "seed": 1},
        "deep": {"model": "m-deep", "temperature": 0.1, "thinking": True},
    },
    "roles": {"gm_agent": "fast", "default": "fast"},
}


class Tiny(BaseModel):
    action: str
    count: int


class FakeProvider:
    """Replays scripted responses. Records what it was asked for."""

    name = "fake"
    script: list[str] = []
    calls: list[dict] = []
    enforces = True

    def __init__(self):
        self.__class__.calls = []

    def enforces_schema(self) -> bool:
        return self.__class__.enforces

    def chat(self, *, base_url, model, messages, schema, params, timeout):
        self.__class__.calls.append(
            {"model": model, "messages": messages, "schema": schema, "params": params}
        )
        index = len(self.__class__.calls) - 1
        text = self.__class__.script[min(index, len(self.__class__.script) - 1)]
        return RawCompletion(text=text, latency_s=0.01, model=model)

    def list_models(self, base_url, timeout=10.0):
        from mironba.llm.providers.base import ModelInfo

        return [ModelInfo(model_id="m-fast", quantization="Q4_K_M")]


@pytest.fixture
def fake_provider(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "fake", FakeProvider)
    return FakeProvider


@pytest.fixture
def run(tmp_path):
    manifest = build_manifest(
        model_id="m-fast",
        server="fake",
        base_url="http://x",
        prompt_template_hash="h",
        snapshot_date="2026-07-30",
    )
    return Run.start(manifest, runs_dir=tmp_path)


@pytest.fixture
def client(run, fake_provider):
    return LLMClient(run, config=CONFIG)


class TestProfileResolution:
    def test_a_role_resolves_through_to_a_model(self):
        cfg = resolve_profile(CONFIG, "gm_agent")
        assert cfg.model == "m-fast"
        assert cfg.name == "fast"

    def test_a_profile_name_also_works_directly(self):
        assert resolve_profile(CONFIG, "deep").model == "m-deep"

    def test_defaults_fill_in_but_never_override(self):
        cfg = resolve_profile(CONFIG, "fast")
        assert cfg.base_url == "http://x"      # from defaults
        assert cfg.temperature == 0.8          # from the profile

    def test_an_unknown_role_names_what_is_available(self):
        with pytest.raises(LLMError, match="Profiles:"):
            resolve_profile(CONFIG, "nope")

    def test_agents_name_jobs_not_models(self):
        """The indirection is the point: M5 swaps models by editing config."""
        real = client_module.load_config()
        assert "gm_agent" in real["roles"]
        assert real["roles"]["gm_agent"] in real["profiles"]


class TestSchemaIsPassedToTheServer:
    def test_the_pydantic_schema_reaches_the_provider(self, client, fake_provider):
        fake_provider.script = ['{"action":"go","count":1}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        sent = fake_provider.calls[0]["schema"]
        assert sent["properties"]["action"]["type"] == "string"

    def test_no_schema_means_no_constraint(self, client, fake_provider):
        fake_provider.script = ["plain text"]
        out = client.complete([{"role": "user", "content": "hi"}])
        assert out == "plain text"
        assert fake_provider.calls[0]["schema"] is None

    def test_the_schema_also_goes_in_the_prompt_when_enforcement_is_unverified(
        self, client, fake_provider
    ):
        """Defence 1b, added after the first live run.

        Ollama accepted `format` and ignored it, and because the client trusted
        that claim the schema never reached the prompt either. The model was
        left guessing field names with no constraint at all, and duly invented
        `{"trade": {"sent_ids": [...]}}`.
        """
        fake_provider.script = ['{"action":"go","count":1}']
        client.schema_enforcement = False
        client.complete([{"role": "user", "content": "decide"}], schema=Tiny)
        sent = fake_provider.calls[0]["messages"][-1]["content"]
        assert "decide" in sent           # the instruction survives
        assert '"count"' in sent          # and now carries the field names

    def test_a_verified_server_does_not_pay_for_the_prompt_copy(
        self, client, fake_provider
    ):
        fake_provider.script = ['{"action":"go","count":1}']
        client.schema_enforcement = True   # as measured by llm/probe.py
        client.complete([{"role": "user", "content": "decide"}], schema=Tiny)
        assert fake_provider.calls[0]["messages"][-1]["content"] == "decide"

    def test_sampling_params_are_carried_through(self, client, fake_provider):
        fake_provider.script = ['{"action":"go","count":1}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        params: SamplingParams = fake_provider.calls[0]["params"]
        assert params.temperature == 0.8
        assert params.seed == 1


class TestRepairRetry:
    def test_a_valid_first_answer_costs_one_call(self, client, fake_provider):
        fake_provider.script = ['{"action":"go","count":1}']
        result = client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert result.count == 1
        assert client.stats.calls == 1
        assert client.stats.first_attempt_failures == 0
        assert len(fake_provider.calls) == 1

    def test_one_repair_retry_on_invalid_json(self, client, fake_provider):
        fake_provider.script = ["not json at all", '{"action":"go","count":2}']
        result = client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert result.count == 2
        assert client.stats.first_attempt_failures == 1
        assert client.stats.repairs_succeeded == 1
        assert client.stats.gave_up == 0

    def test_the_repair_prompt_feeds_the_error_back_verbatim(
        self, client, fake_provider
    ):
        """A summarised error gives the model nothing to act on."""
        fake_provider.script = ['{"action":"go"}', '{"action":"go","count":3}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        repair = fake_provider.calls[1]["messages"][-1]["content"]
        assert "count" in repair          # pydantic named the missing field
        assert "schema" in repair.lower()

    def test_it_gives_up_after_exactly_one_repair(self, client, fake_provider):
        """Then fails loudly. Looping until it stumbles into valid JSON would
        make the measured failure rate a function of our patience."""
        fake_provider.script = ["garbage", "still garbage"]
        with pytest.raises(SchemaFailure) as exc:
            client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert len(fake_provider.calls) == 2
        assert len(exc.value.attempts) == 2
        assert client.stats.gave_up == 1

    def test_the_failure_carries_both_attempts_for_inspection(
        self, client, fake_provider
    ):
        fake_provider.script = ["garbage", "still garbage"]
        with pytest.raises(SchemaFailure) as exc:
            client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert "garbage" in exc.value.attempts[0]["text"]

    def test_a_code_fence_is_unwrapped_not_counted_as_a_failure(
        self, client, fake_provider
    ):
        """Some servers add a fence regardless of schema enforcement."""
        fake_provider.script = ['```json\n{"action":"go","count":4}\n```']
        result = client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert result.count == 4
        assert client.stats.first_attempt_failures == 0

    def test_fence_stripping_does_not_repair_content(self):
        assert _strip_code_fence("```json\n{}\n```") == "{}"
        assert _strip_code_fence("  {}  ") == "{}"
        assert _strip_code_fence("prefix {} suffix") == "prefix {} suffix"


class TestRawLogging:
    def test_every_completion_is_logged_including_failures(
        self, client, fake_provider, run
    ):
        fake_provider.script = ["garbage", '{"action":"go","count":5}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        rows = [
            json.loads(line)
            for line in (run.dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 2
        assert rows[0]["ok"] is False
        assert rows[0]["validation_error"]
        assert rows[1]["ok"] is True

    def test_each_logged_call_carries_the_run_id(self, client, fake_provider, run):
        fake_provider.script = ['{"action":"go","count":1}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        row = json.loads(
            (run.dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert row["run_id"] == run.run_id

    def test_the_log_records_which_defences_were_actually_in_play(
        self, client, fake_provider, run
    ):
        """Otherwise a failure rate cannot be attributed.

        Grammar-constrained decoding and prompt-instructed JSON produce very
        different numbers, and reporting one as the other is the easiest
        mistake to make here — we made it on the first live run.
        """
        fake_provider.script = ['{"action":"go","count":1}']
        client.schema_enforcement = True
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        row = json.loads(
            (run.dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()[0]
        )
        assert row["schema_sent_to_server"] is True
        assert row["schema_enforcement_observed"] is True
        assert row["schema_in_prompt"] is False

    def test_a_client_cannot_exist_without_a_run(self):
        with pytest.raises(ManifestError, match="Run"):
            LLMClient(object(), config=CONFIG)


class TestStats:
    def test_failure_rate_counts_first_attempts_not_final_outcomes(
        self, client, fake_provider
    ):
        """The repair is our mitigation. Folding it in would measure us."""
        fake_provider.script = ["garbage", '{"action":"go","count":1}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert client.stats.schema_failure_rate == 1.0
        assert client.stats.unrecovered_rate == 0.0

    def test_latency_is_recorded_per_attempt(self, client, fake_provider):
        fake_provider.script = ["garbage", '{"action":"go","count":1}']
        client.complete([{"role": "user", "content": "hi"}], schema=Tiny)
        assert len(client.stats.latencies) == 2


class TestSchemasAreSmallAndSafe:
    @pytest.mark.parametrize("schema", AGENT_SCHEMAS, ids=lambda s: s.__name__)
    def test_no_schema_lets_a_model_state_a_salary(self, schema):
        """The boundary rule, enforced at the shape of the form.

        A model that can type into a `salary` field can hallucinate a trade
        into legality, and the validator would have no way to know. Every
        figure comes from the snapshot instead.
        """
        blob = json.dumps(schema.model_json_schema()).lower()
        for token in FORBIDDEN_FIELD_TOKENS:
            assert f'"{token}"' not in blob, (
                f"{schema.__name__} exposes a {token!r} field to the model"
            )

    @pytest.mark.parametrize("schema", AGENT_SCHEMAS, ids=lambda s: s.__name__)
    def test_schemas_stay_flat(self, schema):
        """Two-step selection exists so no schema needs to nest.

        A nested trade inside a decision is the shape the charter forbids, and
        it is the shape a 3B-active model fails on.
        """
        blob = schema.model_json_schema()
        assert "$defs" not in blob, (
            f"{schema.__name__} emits $defs. Grammar-constrained decoders vary "
            "in $ref support, and a schema the server cannot compile silently "
            "degrades to no constraint. Use Literal, not Enum."
        )
        for spec in blob.get("properties", {}).values():
            assert spec.get("type") != "object", f"{schema.__name__} nests an object"

    @pytest.mark.parametrize("schema", AGENT_SCHEMAS, ids=lambda s: s.__name__)
    def test_no_bound_exceeds_what_a_grammar_compiler_will_take(self, schema):
        """A schema is only a defence if the server can compile it.

        ``maxLength: 4000`` on the ``reason`` fields made Ollama 0.32.5 reject
        every request outright:

            HTTP 400 "Failed to initialize samplers: failed to parse grammar"

        Bisected on this machine, 1999 compiles and 2000 does not — the
        compiler appears to expand the bound into a bounded repetition and give
        up past a ceiling. The failure is loud, which is the good case, but it
        is loud at *call* time and hermetic tests never saw it. This one does.

        The ceiling here is deliberately below the observed cliff rather than
        at it. Sitting on a boundary that another server may place elsewhere is
        how this recurs on the next backend.
        """
        SAFE_MAX = 1_600
        blob = schema.model_json_schema()

        def walk(node, path="") -> None:
            if isinstance(node, dict):
                for key in ("maxLength", "maxItems", "maxProperties"):
                    if key in node:
                        assert node[key] <= SAFE_MAX, (
                            f"{schema.__name__}{path}.{key} = {node[key]}, over "
                            f"the {SAFE_MAX} ceiling a grammar compiler accepts"
                        )
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{path}[{i}]")

        walk(blob)

    def test_the_action_enum_is_inlined(self):
        spec = ActionChoice.model_json_schema()["properties"]["action"]
        assert spec.get("enum") == ["propose_trade", "stand_pat"]

    def test_an_intent_names_wants_by_id_only(self):
        props = set(TradeIntent.model_json_schema()["properties"])
        assert props == {
            "target_player_ids",
            "tradeable_asset_ids",
            "excluded_player_ids",
            "priority",
            "reason",
        }


class TestProviderBoundary:
    def test_unknown_server_names_the_alternatives(self):
        with pytest.raises(ProviderError, match="openai_compatible"):
            provider_for("nonsense")

    def test_every_registered_provider_satisfies_the_contract(self):
        for name, cls in PROVIDERS.items():
            provider = cls()
            assert hasattr(provider, "chat"), name
            assert hasattr(provider, "list_models"), name
            assert isinstance(provider.enforces_schema(), bool), name

    def test_no_provider_claims_enforcement_it_has_not_verified(self):
        """Servers that accept a schema parameter and ignore it exist.

        Ollama 0.31.1 is one: it took `format` with a full JSON schema and
        returned an object with entirely different field names. It used to
        return True here, which suppressed the prompt fallback and logged a
        claim the raw completions contradict.
        """
        assert provider_for("vllm").enforces_schema() is False
        assert provider_for("ollama").enforces_schema() is False

    def test_no_provider_specific_code_outside_providers(self):
        """The charter's hard boundary, checked by grep.

        Model-agnostic by construction means the rest of the codebase cannot
        name a server. If this fails, the abstraction has already leaked.
        """
        root = Path(__file__).resolve().parents[1] / "mironba"
        names = ("ollama", "vllm", "sglang", "llama.cpp", "lmstudio", "openai")
        offenders = []
        for path in root.rglob("*.py"):
            if "providers" in path.parts:
                continue
            text = path.read_text(encoding="utf-8").lower()
            code = "\n".join(
                line
                for line in text.splitlines()
                if not line.lstrip().startswith("#")
            )
            # Docstrings legitimately discuss servers; import statements and
            # branching on a server name do not.
            for name in names:
                for line in code.splitlines():
                    stripped = line.strip()
                    if name in stripped and (
                        stripped.startswith(("import ", "from "))
                        or f'== "{name}"' in stripped
                        or f"== '{name}'" in stripped
                    ):
                        offenders.append(f"{path.name}: {stripped}")
        assert not offenders, "provider-specific code outside llm/providers/:\n" + "\n".join(
            offenders
        )
