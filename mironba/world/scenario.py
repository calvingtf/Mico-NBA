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
SCENARIO_DEBT = (
    "mironba/sim/branch.py",
    "mironba/sim/league.py",
    "mironba/sim/arrivals.py",
    "mironba/world/pending.py",
    "mironba/eval/backtest.py",
    "mironba/eval/branch_score.py",
    "mironba/eval/interest_score.py",
    "mironba/report/evidence_view.py",
)


class ScenarioError(ValueError):
    """A scenario declared incompletely. Never patched with a default."""


@dataclass(frozen=True)
class BranchScenario:
    id: str
    season: str
    freeze: date
    freeze_rationale: str
    subjects: tuple[str, ...]
    decision: str
    branches: tuple[str, ...]
    actual_branch: str
    scored_teams: tuple[str, ...]
    evidence_dir: Path = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        missing = [
            name for name in ("id", "season", "freeze_rationale", "decision")
            if not getattr(self, name)
        ]
        if missing:
            raise ScenarioError(f"scenario is missing {missing}")
        if self.actual_branch not in self.branches:
            raise ScenarioError(
                f"{self.id}: actual_branch {self.actual_branch!r} is not one "
                f"of the declared branches {self.branches}"
            )
        if not self.subjects or not self.scored_teams:
            raise ScenarioError(f"{self.id}: subjects and scored_teams required")
        if self.evidence_dir is None:
            object.__setattr__(self, "evidence_dir", EVIDENCE_ROOT / self.id)

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
        branches=tuple(raw["branches"]),
        actual_branch=raw["actual_branch"],
        scored_teams=tuple(raw["scored_teams"]),
    )
