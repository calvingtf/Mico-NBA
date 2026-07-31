"""Run manifests: what produced a result, recorded before the result exists.

M5 compares models on the same scenario and seed. That comparison is only
possible if every run already carries the model id, quantization, sampling
parameters, prompt-template hash and code revision that produced it — and it
has to be *already*, because a run whose provenance is reconstructed afterwards
is a guess wearing a timestamp. Runs made before this module existed would be
worthless to M5, which is why it is the first thing in M1.

The design rule here is that provenance cannot be skipped rather than merely
should not be. There is no public way to write a run artifact except through a
``Run``, and a ``Run`` cannot exist without a complete ``RunManifest``. See
``Run.start`` and ``Run.write_json``.

Nothing in this module imports an LLM. A run of pure deterministic code gets a
manifest on the same terms — ``model_id="none"`` is a legitimate value, and M0's
validator could be driven under one unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Bumped when the shape of manifest.json changes in a way that breaks readers.
#: M5 will read manifests written months apart; it needs to know what it has.
MANIFEST_SCHEMA_VERSION = 1

DEFAULT_RUNS_DIR = Path("runs")


class ManifestError(ValueError):
    """A run was described incompletely. Never downgraded to a warning."""


def _git(*args: str) -> str | None:
    """Best-effort git query. Returns None outside a repo or without git."""
    try:
        out = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=Path(__file__).resolve().parents[2],
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_revision() -> tuple[str, bool]:
    """``(commit_sha, dirty)`` for the working tree that is running.

    ``dirty`` matters more than it looks. A result produced from uncommitted
    code cannot be reproduced from the sha alone, so recording the sha without
    the flag would assert reproducibility we do not have. An M5 comparison
    should discard dirty runs, and it can only do that if we say so.
    """
    sha = _git("rev-parse", "HEAD")
    if sha is None:
        return ("unknown", True)
    status = _git("status", "--porcelain")
    return (sha, bool(status))


def template_hash(*templates: str) -> str:
    """Stable hash of the exact prompt text used.

    Prompt wording changes results as surely as temperature does, and it is the
    parameter most likely to drift without anyone recording it. Hashing the
    literal template strings means a reworded prompt cannot silently be
    compared against results from the old wording.
    """
    digest = hashlib.sha256()
    for text in templates:
        digest.update(text.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class RunManifest:
    """Everything needed to reproduce, or refuse to trust, a single run."""

    run_id: str
    started_at: str

    # Model and server
    model_id: str
    quantization: str
    server: str
    base_url: str

    # Sampling
    temperature: float
    top_p: float
    seed: int | None
    thinking: bool

    # Code and inputs
    prompt_template_hash: str
    schema_version: int
    snapshot_date: str
    git_commit_sha: str
    git_dirty: bool

    scenario_id: str = ""
    profile: str = ""
    notes: str = ""
    manifest_schema_version: int = MANIFEST_SCHEMA_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    #: Fields that make a run identifiable at all. Blank means "we did not
    #: record it", and a blank here is what makes a result untraceable later.
    REQUIRED = (
        "run_id",
        "started_at",
        "model_id",
        "server",
        "prompt_template_hash",
        "snapshot_date",
        "git_commit_sha",
    )

    def __post_init__(self) -> None:
        missing = [
            name
            for name in self.REQUIRED
            if not str(getattr(self, name) or "").strip()
        ]
        if missing:
            raise ManifestError(
                f"manifest is missing required field(s): {', '.join(missing)}. "
                "A run that cannot say what produced it is not comparable in "
                "M5, and an artifact from it cannot be trusted."
            )
        if self.seed is None and self.temperature > 0:
            # Not fatal: some servers ignore seed. But it must be visible.
            object.__setattr__(
                self,
                "notes",
                (self.notes + " | " if self.notes else "")
                + "no seed with temperature>0: run is not reproducible",
            )

    @property
    def reproducible(self) -> bool:
        """Whether replaying this manifest could be expected to reproduce it."""
        return (
            not self.git_dirty
            and self.git_commit_sha != "unknown"
            and (self.seed is not None or self.temperature == 0)
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["reproducible"] = self.reproducible
        return data

    def summary(self) -> str:
        seed = "none" if self.seed is None else self.seed
        flag = "" if self.reproducible else "   [NOT REPRODUCIBLE]"
        return (
            f"run {self.run_id}{flag}\n"
            f"  model      {self.model_id} ({self.quantization}) on {self.server}"
            f" @ {self.base_url}\n"
            f"  sampling   temp={self.temperature} top_p={self.top_p} "
            f"seed={seed} thinking={self.thinking}\n"
            f"  prompts    template={self.prompt_template_hash} "
            f"schema_v={self.schema_version}\n"
            f"  inputs     snapshot={self.snapshot_date} scenario={self.scenario_id or '-'}\n"
            f"  code       {self.git_commit_sha[:12]}"
            f"{' (dirty)' if self.git_dirty else ''}\n"
            f"  started    {self.started_at}"
            + (f"\n  notes      {self.notes}" if self.notes else "")
        )


class Run:
    """A directory of artifacts that all carry one run id.

    This is the only supported way to write a run artifact. ``write_json``
    stamps ``run_id`` into every object it writes, so an artifact separated
    from its directory still knows where it came from — which is the case that
    actually happens, when someone copies one JSON file out to look at it.
    """

    def __init__(self, manifest: RunManifest, directory: Path) -> None:
        if not isinstance(manifest, RunManifest):
            raise ManifestError(
                f"a Run requires a RunManifest, got {type(manifest).__name__}. "
                "Artifacts cannot be written outside a manifested run."
            )
        self.manifest = manifest
        self.dir = directory

    @classmethod
    def start(
        cls,
        manifest: RunManifest,
        runs_dir: Path | str = DEFAULT_RUNS_DIR,
    ) -> Run:
        """Create the run directory and write manifest.json into it first."""
        directory = Path(runs_dir) / manifest.run_id
        directory.mkdir(parents=True, exist_ok=True)
        run = cls(manifest, directory)
        # Manifest before anything else: if the process dies mid-run, the
        # partial artifacts left behind are still attributable.
        run.write_json("manifest.json", manifest.to_dict(), _stamp=False)
        return run

    @property
    def run_id(self) -> str:
        return self.manifest.run_id

    def path(self, name: str) -> Path:
        """Path to an artifact inside this run. Refuses to escape the run dir."""
        target = (self.dir / name).resolve()
        root = self.dir.resolve()
        if root != target and root not in target.parents:
            raise ManifestError(
                f"artifact {name!r} would be written outside run {self.run_id}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, name: str, obj: Any, *, _stamp: bool = True) -> Path:
        if _stamp and isinstance(obj, dict):
            obj = {"run_id": self.run_id, **obj}
        target = self.path(name)
        target.write_text(
            json.dumps(obj, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return target

    def append_jsonl(self, name: str, obj: dict) -> Path:
        target = self.path(name)
        line = json.dumps({"run_id": self.run_id, **obj}, ensure_ascii=False, default=str)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return target

    def write_text(self, name: str, text: str) -> Path:
        target = self.path(name)
        target.write_text(text, encoding="utf-8")
        return target


def new_run_id(prefix: str = "") -> str:
    """Sortable, unique, and readable in a directory listing."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    return f"{prefix + '-' if prefix else ''}{stamp}-{suffix}"


def build_manifest(
    *,
    model_id: str,
    server: str,
    base_url: str,
    prompt_template_hash: str,
    snapshot_date: str,
    quantization: str = "unknown",
    temperature: float = 0.0,
    top_p: float = 1.0,
    seed: int | None = None,
    thinking: bool = False,
    schema_version: int = 1,
    scenario_id: str = "",
    profile: str = "",
    notes: str = "",
    run_id: str | None = None,
    **extra: Any,
) -> RunManifest:
    """Assemble a manifest, filling in git revision and wall-clock start."""
    sha, dirty = git_revision()
    return RunManifest(
        run_id=run_id or new_run_id(scenario_id.replace("/", "-") if scenario_id else ""),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
        model_id=model_id,
        quantization=quantization,
        server=server,
        base_url=base_url,
        temperature=temperature,
        top_p=top_p,
        seed=seed,
        thinking=thinking,
        prompt_template_hash=prompt_template_hash,
        schema_version=schema_version,
        snapshot_date=snapshot_date,
        git_commit_sha=sha,
        git_dirty=dirty,
        scenario_id=scenario_id,
        profile=profile,
        notes=notes,
        extra=dict(extra) if extra else {},
    )


def load_manifest(path: Path | str) -> RunManifest:
    """Read a manifest back, for M5 and for anyone auditing an old run."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    data.pop("reproducible", None)
    known = {f for f in RunManifest.__slots__}
    unknown = {k: v for k, v in data.items() if k not in known}
    kept = {k: v for k, v in data.items() if k in known}
    if unknown:
        kept.setdefault("extra", {}).update(unknown)
    return RunManifest(**kept)


def env_default(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)
