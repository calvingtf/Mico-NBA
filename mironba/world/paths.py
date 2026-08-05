"""Path components that come from data, and how each is known to be a string.

A scenario id spent one day as an int. Written unquoted into yaml, `id: 2026`
parsed back as a number, and `EVIDENCE_ROOT / self.id` raised

    TypeError: unsupported operand type(s) for /: 'WindowsPath' and 'int'

which the authoring round-trip caught with a blanket ``except Exception`` and
reported as "drafted yaml would not load as a scenario" - a crash wearing a
verdict's clothes. The same id worked fine everywhere it was joined through an
f-string, because formatting accepts anything. That is the shape of the
defect: **it fails only on the joins that pass the value bare**, so one code
path crashes while its neighbours look healthy.

So the bare joins are enumerated. ``PATH_COMPONENT_SITES`` lists every place
in the package where a ``Path`` is divided by a name rather than by a literal
or an f-string, with a stated reason why that name is a string by the time it
arrives. A test AST-scans for those joins and fails if one appears that is not
listed - the same fence as the writer registry and the findings dispositions.

An f-string join is not listed and not safe by accident: it cannot raise
TypeError, but ``f"bbref-{season}"`` with a numeric season still builds a
path nobody meant. Those are a different and quieter problem, and the reason
this module says "how it is a string" rather than "it cannot crash".
"""

from __future__ import annotations

from pathlib import Path


class PathComponentError(ValueError):
    """A value cannot be used as a path component.

    ValueError, not TypeError, and the choice matters. Three of the four
    checks below are about the VALUE - empty, padded, contains a separator -
    and only the first is about the type. More practically, every caller
    that guards a path component already catches ValueError:
    ``runner.start`` documents it, and ``api/ui.py`` turns it into a clean
    message. Raising a TypeError here would have sailed past that handler
    and become a 500, which is the same swap this whole change is about -
    an internal failure presented as something it is not.
    """


def as_component(value, what: str) -> str:
    """Assert ``value`` is a usable path component, and return it.

    Deliberately not ``str(value)``. Coercing would make ``2026`` and
    ``"2026"`` produce the same directory, which is how a numeric id gets a
    second life as a filename instead of being rejected at the boundary
    where somebody can still fix it.
    """
    if not isinstance(value, str):
        raise PathComponentError(
            f"{what} must be a string to be used as a path component; got "
            f"{type(value).__name__} {value!r}. Quote it at the source - a "
            "bare number in yaml or json arrives here as a number and only "
            "fails on the joins that pass it bare."
        )
    if not value or value.strip() != value:
        raise PathComponentError(
            f"{what} must be non-empty and unpadded; got {value!r}")
    if value in (".", "..") or "/" in value or "\\" in value:
        raise PathComponentError(
            f"{what} must be a single path component; got {value!r}")
    return value


#: (module, attribute) -> how the value is guaranteed to be a string.
#: Exhaustive over the package's bare ``Path / name`` joins, by test - in
#: BOTH directions, so a join that is fixed (wrapped in ``as_component``)
#: must be removed from here too. A registry that keeps naming code that no
#: longer exists is how a reader comes to trust a list that has stopped
#: describing anything: ``data/ingest/rss.py`` was in this table for exactly
#: as long as it took to guard it.
PATH_COMPONENT_SITES: dict = {
    ("api/runner.py", "run_id"):
        "generated in runner.start() from an f-string of a validated "
        "scenario id, a UTC timestamp and a uuid hex - a str by "
        "construction, and never taken from a request",
    ("api/ui.py", "run_id"):
        "a FastAPI path parameter annotated `run_id: str`, so the framework "
        "has already rejected anything else; the handlers additionally "
        "refuse '..' before joining",
    ("data/candidates.py", "snapshot_id"):
        "a snapshot directory name read from the filesystem listing, so it "
        "is a str from Path.name",
    ("data/ingest/nba_stats.py", "name"):
        "a literal filename chosen inside the writer loop",
    ("eval/branch_score.py", "backtest"):
        "a Path already, not a component - the join is Path / Path",
    ("sim/scenario.py", "snapshot"):
        "a snapshot directory name from the caller's own glob",
    ("world/manifest.py", "name"):
        "a literal artifact filename passed by the writer",
    ("world/scenario.py", "id"):
        "BranchScenario.__post_init__ raises ScenarioError before the join "
        "if the id is not a str - THE fix for the WindowsPath / int crash",
    ("world/scenario.py", "name"):
        "an artifact filename supplied by the caller, from a literal",
}


def evidence_dir_for(scenario_id, root: Path) -> Path:
    """The evidence directory for a scenario id, guarded."""
    return root / as_component(scenario_id, "scenario id")
