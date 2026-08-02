"""A branch scenario as a declared object, not a constellation of constants.

The enumeration that preceded this module found **89 scenario-bound
occurrences across 14 files** - freeze dates, subject ids, branch names,
evidence paths, scored-team tuples - all assuming the LeBron 2026 case. Same
move as the writer registry and the derivation registry: make the surface
enumerable first, migrate second, and never report the migration as complete
while the debt list is non-empty.

One YAML file under ``configs/branch/`` declares everything scenario-specific:

* id, season, the freeze timestamp **and why that instant** - a scenario must
  state its own boundary rationale the way lebron-2026 states the moratorium;
* subjects: players and teams in scope, used to filter ingest and evidence;
* the decision: what is unresolved, and the named branch set;
* scored teams: who gets precision/recall;
* the evidence directory, keyed by scenario id.

``SCENARIO_DEBT`` lists every module still holding scenario identifiers
outside a scenario file. The fence test fails if an identifier appears in a
module NOT on the list - new leakage is caught - and the list shrinking to
empty is the definition of "the branch path is scenario-general", which is a
claim the README does not make until a second scenario runs with zero code
changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "branch"
EVIDENCE_ROOT = Path(__file__).resolve().parents[2] / "evidence"

#: Modules that still hard-code scenario identifiers, found by enumeration
#: (89 occurrences, 14 files). Tracked, not hidden: the fence test allows
#: these and only these, so migration is measurable and new leakage fails.
SCENARIO_DEBT: tuple = ()  # paid in full; the fence now admits no module


class ScenarioError(ValueError):
    """A scenario declared incompletely. Never patched with a default."""


@dataclass(frozen=True)
class BranchScenario:
    """One declared scenario. Two seed shapes share this object:

    * ``pending_decision`` - something is unresolved (where does a player
      sign?), the world forks into branches, one branch happened.
    * ``stipulated`` - the event is asserted up front (Curry traded to the
      Lakers), there are no branches and no ground truth, and the run is a
      demonstration labelled unfalsifiable, never a measurement.

    They share: id, season, a freeze with a stated rationale, subjects, an
    evidence directory, scored teams (empty for stipulated - nothing scores).
    They diverge on: branches/actual_branch (pending only) versus
    ``stipulation`` (stipulated only), and on whether eval may run at all.
    """

    id: str
    season: str
    freeze: date
    freeze_rationale: str
    subjects: tuple[str, ...]
    decision: str
    branches: tuple[str, ...]
    actual_branch: str
    scored_teams: tuple[str, ...]
    kind: str = "pending_decision"
    #: The player whose unresolved decision forks the world (pending only).
    decision_subject: str = ""
    #: The team holding capacity for the subject, and the branch where the
    #: subject joins it.
    blocker_team: str = ""
    blocker_branch: str = ""
    #: branch -> lowercase marker; a conditional fires in the branch whose
    #: marker appears in its condition. One declared rule, no inference.
    condition_markers: dict = field(default_factory=dict)
    #: The league year the decision lands in ("2026-27" for a July-2026 freeze).
    next_season: str = ""
    #: Structured persona params per team; anything absent uses the default.
    personas: dict = field(default_factory=dict)
    #: Stipulated seed only: the asserted event, validated by rules/ first.
    stipulation: dict = field(default_factory=dict)
    evidence_dir: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        missing = [
            name for name in ("id", "season", "freeze_rationale", "decision")
            if not getattr(self, name)
        ]
        if missing:
            raise ScenarioError(f"scenario is missing {missing}")
        if self.kind == "stipulated":
            if self.branches or self.actual_branch:
                raise ScenarioError(
                    f"{self.id}: a stipulated scenario has no branches - the "
                    "event is asserted, not forked"
                )
            if not self.stipulation:
                raise ScenarioError(f"{self.id}: stipulated but no stipulation")
        elif self.actual_branch not in self.branches:
            raise ScenarioError(
                f"{self.id}: actual_branch {self.actual_branch!r} is not one "
                f"of the declared branches {self.branches}"
            )
        if not self.subjects or not self.scored_teams:
            raise ScenarioError(f"{self.id}: subjects and scored_teams required")
        if self.evidence_dir is None:
            object.__setattr__(self, "evidence_dir", EVIDENCE_ROOT / self.id)

    def condition_fires_in(self, condition: str, branch: str) -> bool:
        """The declared per-scenario rule; entry 44 is what inference does."""
        marker = self.condition_markers.get(branch, "")
        others = [m for b, m in self.condition_markers.items() if b != branch and m]
        if marker:
            return marker in condition.lower()
        return not any(m in condition.lower() for m in others)

    def _data_rows(self, name: str) -> list[dict]:
        import csv

        path = self.evidence_dir / name
        if not path.is_file():
            return []
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def pre_freeze_arrival_ids(self) -> set[str]:
        """Hand-curated pre-freeze arrivals - data in the store, not code."""
        return {r["player_id"] for r in self._data_rows("pre-freeze-arrivals.csv")}

    def post_freeze_signing_ids(self) -> set[str]:
        """Names whose signings postdate the freeze; excluded from freeze state."""
        return {r["player_id"] for r in self._data_rows("post-freeze-signings.csv")}

    def ledger(self):
        """The scenario's evidence, PRE/POST partitioned by ITS OWN freeze.

        The partition comes from the scenario's declared date, never from
        which file a row sits in, and never from a module constant.
        """
        from mironba.world.evidence import load_ledger

        return load_ledger(self.evidence_dir, self.id, self.freeze)


def load_scenario(scenario_id: str) -> BranchScenario:
    """Load a declared scenario. No default: a caller must name one."""
    path = CONFIG_DIR / f"{scenario_id}.yaml"
    if not path.is_file():
        known = sorted(p.stem for p in CONFIG_DIR.glob("*.yaml"))
        raise ScenarioError(
            f"no scenario {scenario_id!r} under {CONFIG_DIR}; declared: {known}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return BranchScenario(
        id=raw["id"],
        season=raw["season"],
        freeze=date.fromisoformat(str(raw["freeze"])),
        freeze_rationale=raw["freeze_rationale"],
        subjects=tuple(raw["subjects"]),
        decision=raw["decision"],
        branches=tuple(raw.get("branches") or ()),
        actual_branch=raw.get("actual_branch", ""),
        scored_teams=tuple(raw.get("scored_teams") or ()),
        kind=raw.get("kind", "pending_decision"),
        decision_subject=raw.get("decision_subject", ""),
        blocker_team=raw.get("blocker_team", ""),
        blocker_branch=raw.get("blocker_branch", ""),
        condition_markers=dict(raw.get("condition_markers") or {}),
        next_season=raw.get("next_season", ""),
        personas=dict(raw.get("personas") or {}),
        stipulation=dict(raw.get("stipulation") or {}),
    )
