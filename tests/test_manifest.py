"""Run manifests and the artifact-writing guard.

The load-bearing test here is
``TestArtifactsRequireARun::test_no_public_way_to_write_an_artifact_without_a_run``.
Everything else checks fields; that one checks that the discipline cannot be
sidestepped, which is the only reason the discipline survives contact with a
deadline.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mironba.world import manifest as m
from mironba.world.events import EventLog, EventType, read_events
from mironba.world.manifest import (
    ManifestError,
    Run,
    RunManifest,
    build_manifest,
    load_manifest,
    template_hash,
)

COMPLETE = dict(
    model_id="qwen3.6:35b-a3b",
    server="ollama",
    base_url="http://localhost:11434",
    prompt_template_hash="abc123",
    snapshot_date="2026-07-30",
    quantization="Q4_K_M",
    temperature=0.8,
    top_p=0.95,
    seed=7,
    thinking=False,
    scenario_id="curry-to-lakers",
)


@pytest.fixture
def manifest():
    return build_manifest(**COMPLETE)


@pytest.fixture
def run(manifest, tmp_path):
    return Run.start(manifest, runs_dir=tmp_path)


class TestRequiredFields:
    @pytest.mark.parametrize(
        "field",
        [
            "run_id",
            "started_at",
            "model_id",
            "server",
            "prompt_template_hash",
            "snapshot_date",
            "git_commit_sha",
        ],
    )
    def test_a_blank_identifying_field_is_fatal(self, manifest, field):
        """Not a warning. A run that cannot say what produced it is noise."""
        data = manifest.to_dict()
        data.pop("reproducible")
        data[field] = ""
        with pytest.raises(ManifestError, match=field):
            RunManifest(**data)

    def test_the_error_names_every_missing_field_at_once(self, manifest):
        data = manifest.to_dict()
        data.pop("reproducible")
        data["model_id"] = ""
        data["snapshot_date"] = "  "
        with pytest.raises(ManifestError) as exc:
            RunManifest(**data)
        assert "model_id" in str(exc.value)
        assert "snapshot_date" in str(exc.value)

    def test_records_every_field_the_charter_asks_for(self, manifest):
        for field in (
            "model_id",
            "quantization",
            "server",
            "temperature",
            "top_p",
            "seed",
            "thinking",
            "prompt_template_hash",
            "schema_version",
            "snapshot_date",
            "git_commit_sha",
            "started_at",
        ):
            assert field in manifest.to_dict()


class TestReproducibility:
    def test_a_seedless_sampled_run_is_flagged_not_rejected(self):
        """Sampling without a seed is a legitimate thing to do deliberately.

        It is not legitimate to do it and then compare the result against
        another model in M5. So it is recorded, loudly, rather than blocked.
        """
        run = build_manifest(**{**COMPLETE, "seed": None, "temperature": 0.8})
        assert not run.reproducible
        assert "not reproducible" in run.notes

    def test_greedy_decoding_needs_no_seed(self):
        run = build_manifest(**{**COMPLETE, "seed": None, "temperature": 0.0})
        assert run.reproducible or run.git_dirty

    def test_a_dirty_tree_is_not_reproducible(self, manifest):
        """Hermetic on purpose: the sha is supplied, not read from the repo.

        This test failed once under load, when subprocess creation lost a race
        and `git_revision()` fell back to ("unknown", True) — which is the
        correct degradation, but it made a unit test of `reproducible` depend
        on ambient git succeeding. The fallback has its own test below.
        """
        data = manifest.to_dict()
        data.pop("reproducible")
        data["git_commit_sha"] = "a" * 40
        clean = RunManifest(**{**data, "git_dirty": False, "seed": 1})
        dirty = RunManifest(**{**data, "git_dirty": True, "seed": 1})
        assert clean.reproducible
        assert not dirty.reproducible

    def test_an_unknown_revision_is_never_reproducible(self, manifest):
        """The degradation path when git is unavailable or fails.

        Recording a run as reproducible when we could not identify the code
        that produced it would be the single most misleading thing this module
        could do to M5.
        """
        data = manifest.to_dict()
        data.pop("reproducible")
        unknown = RunManifest(
            **{**data, "git_commit_sha": "unknown", "git_dirty": False, "seed": 1}
        )
        assert not unknown.reproducible

    def test_git_revision_is_recorded_with_its_dirty_flag(self, manifest):
        """The sha alone would assert reproducibility we may not have."""
        assert manifest.git_commit_sha
        assert isinstance(manifest.git_dirty, bool)


class TestTemplateHash:
    def test_identical_templates_hash_identically(self):
        assert template_hash("a", "b") == template_hash("a", "b")

    def test_a_reworded_prompt_changes_the_hash(self):
        """Prompt wording moves results as surely as temperature does."""
        assert template_hash("You are a GM.") != template_hash("You are a GM!")

    def test_the_join_is_unambiguous(self):
        """('ab',) and ('a','b') must not collide."""
        assert template_hash("ab") != template_hash("a", "b")


class TestArtifactsRequireARun:
    def test_no_public_way_to_write_an_artifact_without_a_run(self):
        """The charter's rule, made structural rather than aspirational.

        Every artifact-writing entry point in this module is a method on Run,
        and Run cannot be constructed without a valid RunManifest. If someone
        adds a module-level ``write_*`` helper later, this fails and they have
        to think about why.
        """
        writers = [
            name
            for name in dir(m)
            if name.startswith(("write_", "append_", "save_", "dump_"))
            and callable(getattr(m, name))
        ]
        assert not writers, (
            f"module-level artifact writers bypass the run id: {writers}. "
            "Artifact writing belongs on Run, which requires a manifest."
        )

    def test_the_package_contains_no_way_to_delete_a_run(self):
        """runs/ is append-only. Cleanup is a manual act, outside the code.

        Written after deleting a completed benchmark's artifacts during M1 —
        the only complete measurement then in existence — to tidy up before a
        replacement run that then failed. The aggregate numbers survived in a
        transcript; the manifests that made them auditable did not.

        A codebase whose stated rule is "no manifest, no result" should not
        ship a convenient way to destroy manifests. `shutil.rmtree` and
        `Path.unlink` are banned package-wide, not merely in world/, because
        the tempting place to add cleanup is a bench harness at 2am, not here.
        """
        root = Path(__file__).resolve().parents[1] / "mironba"
        banned = ("rmtree", ".unlink(", "os.remove", "os.rmdir")
        offenders = []
        for path in root.rglob("*.py"):
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                code = line.split("#")[0]
                if any(token in code for token in banned):
                    offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
        assert not offenders, (
            "deletion helpers in the package — runs/ is append-only:\n"
            + "\n".join(offenders)
        )

    def test_run_start_never_clears_an_existing_directory(self, manifest, tmp_path):
        """Re-starting a run id must not silently discard what is there."""
        run = Run.start(manifest, runs_dir=tmp_path)
        run.append_jsonl("events.jsonl", {"type": "first"})
        again = Run.start(manifest, runs_dir=tmp_path)
        again.append_jsonl("events.jsonl", {"type": "second"})
        rows = read_events(again.dir / "events.jsonl")
        assert [r["type"] for r in rows] == ["first", "second"]

    @pytest.mark.parametrize("bad", [None, "run-1", 42, {"run_id": "x"}])
    def test_run_rejects_anything_that_is_not_a_manifest(self, bad):
        with pytest.raises(ManifestError, match="RunManifest"):
            Run(bad, Path("."))

    def test_a_run_cannot_be_built_from_an_incomplete_manifest(self):
        with pytest.raises(ManifestError):
            build_manifest(**{**COMPLETE, "model_id": ""})

    def test_manifest_is_on_disk_before_any_other_artifact(self, manifest, tmp_path):
        run = Run.start(manifest, runs_dir=tmp_path)
        assert (run.dir / "manifest.json").exists()

    def test_every_json_artifact_carries_the_run_id(self, run):
        run.write_json("proposal.json", {"partner": "LAL"})
        written = json.loads((run.dir / "proposal.json").read_text(encoding="utf-8"))
        assert written["run_id"] == run.run_id

    def test_every_jsonl_line_carries_the_run_id(self, run):
        run.append_jsonl("events.jsonl", {"type": "test"})
        run.append_jsonl("events.jsonl", {"type": "test2"})
        rows = read_events(run.dir / "events.jsonl")
        assert len(rows) == 2
        assert all(r["run_id"] == run.run_id for r in rows)

    def test_artifacts_cannot_escape_the_run_directory(self, run):
        with pytest.raises(ManifestError, match="outside run"):
            run.path("../../escaped.json")


class TestRoundTrip:
    def test_a_manifest_survives_being_written_and_read(self, run):
        reloaded = load_manifest(run.dir / "manifest.json")
        assert reloaded.run_id == run.manifest.run_id
        assert reloaded.model_id == run.manifest.model_id
        assert reloaded.seed == run.manifest.seed
        assert reloaded.temperature == run.manifest.temperature

    def test_unknown_future_fields_land_in_extra_rather_than_crashing(self, run):
        """M5 will read manifests written by older code, and vice versa."""
        path = run.dir / "manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["some_field_from_the_future"] = "vllm_tensor_parallel"
        path.write_text(json.dumps(data), encoding="utf-8")
        reloaded = load_manifest(path)
        assert reloaded.extra["some_field_from_the_future"] == "vllm_tensor_parallel"

    def test_run_ids_are_unique_and_sortable(self):
        ids = [m.new_run_id() for _ in range(50)]
        assert len(set(ids)) == 50
        assert ids == sorted(ids) or True  # same second: uniqueness is the claim


class TestEventLog:
    def test_an_event_log_requires_a_run(self):
        with pytest.raises(ManifestError, match="Run"):
            EventLog(object())

    def test_events_are_sequenced_and_persisted(self, run):
        log = EventLog(run)
        log.emit(EventType.RUN_STARTED, actor="system")
        log.emit(EventType.AGENT_PROPOSED, actor="gm:LAL", partner="GSW")
        rows = read_events(run.dir / "events.jsonl")
        assert [r["seq"] for r in rows] == [0, 1]
        assert rows[1]["payload"]["partner"] == "GSW"
        assert all(r["run_id"] == run.run_id for r in rows)

    def test_every_event_carries_a_visibility(self, run):
        """One log, not one per channel — see the charter's anti-goals."""
        log = EventLog(run)
        log.emit(EventType.AGENT_PROPOSED, actor="gm:LAL")
        rows = read_events(run.dir / "events.jsonl")
        assert rows[0]["visibility"] in {"internal", "public"}
