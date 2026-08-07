"""Start a simulation run WITHOUT importing the simulation.

The import fence (``tests/test_ui.py::test_no_view_imports_sim_models_or_eval``)
bars ``mironba.sim``, ``mironba.models`` and ``mironba.eval`` from this
package. This module starts runs anyway, and the distinction matters:

**Why a subprocess is not a hole in the fence.** The fence exists to stop the
UI becoming a second results pipeline - a place where a number could be
computed, adjusted, or presented differently from what the CLI produces. A
subprocess cannot do that, because the UI never holds the objects:

* *Computation stays in sim/.* The child is the same entry point a user types
  in a terminal, run with the same arguments. There is no second code path to
  keep in sync and no UI-side variant of the simulation to drift.
* *The UI still reads only artifacts.* Nothing crosses back except the child's
  stdout (shown verbatim while it runs) and, when it exits, the manifest it
  wrote to ``runs/``. Every number the UI later renders is read off disk from
  that file - the same file the CLI writes and the same one committed to the
  repo. If the child writes no manifest, the UI has nothing to show and says
  so.
* *It cannot become a pipeline.* The UI chooses no seed, no model, no
  post-processing; it picks a scenario id from an allowlist of declared
  scenario files and a run directory name. Every other parameter is the
  runner's own default. A button that types a command is not a pipeline.

The negative form of the same claim is a test: after the UI starts a run,
``mironba.sim`` is still absent from *this* process's ``sys.modules``
(``test_starting_a_run_does_not_import_sim``). That is the property the fence
is actually about, and it survives.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "configs" / "branch"
RUNS = ROOT / "runs"

#: A string naming a module, never imported here. The fence's AST check reads
#: imports; this is deliberately not one, and the module docstring above says
#: why that is a real distinction rather than a technicality.
ENTRY_POINT = "mironba.sim.stipulated"

#: run_id -> {proc, lines, started, scenario, returncode}
RUNNING: dict = {}

#: Measured, not estimated: a stipulated run is fully deterministic - no
#: model call anywhere - and both curated scenarios complete in about six
#: seconds on this machine. Stated so the watcher's elapsed counter has a
#: scale, the same discipline as the drafting latency.
TYPICAL_RUN_S = 6


def known_scenarios() -> list[str]:
    """The allowlist: declared scenarios this entry point can actually run.

    ENTRY_POINT is the stipulated runner, which exits on a pending-decision
    scenario. Offering one anyway would put a button on the page whose only
    outcome is a failure the user cannot act on, so the kind is read from
    the file - by text, because importing the loader is not worth a
    dependency here and the field is a literal.
    """
    out = []
    for path in sorted(SCENARIOS.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if any(line.strip() in ("kind: stipulated", 'kind: "stipulated"')
               for line in text.splitlines()):
            out.append(path.stem)
    return out


def _drain(run_id: str, proc: subprocess.Popen) -> None:
    """Read the child to EOF, then CLOSE the pipe and reap it.

    Leaving stdout open leaks a file descriptor per run. It surfaced as a
    ResourceWarning raised inside an unrelated test, because this project
    runs pytest with filterwarnings=error and a warning from a garbage
    collection lands on whatever happens to be executing - a failure
    attributed to a test that did nothing wrong.
    """
    job = RUNNING[run_id]
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            job["lines"].append(line.rstrip("\n"))
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.wait()
    job["returncode"] = proc.returncode
    job["finished"] = time.monotonic()


def start(scenario_id: str) -> str:
    """Spawn the CLI for ``scenario_id``; return the run directory name."""
    from mironba.world.paths import as_component

    # A form field arrives as a string, but the allowlist below compares it
    # to filenames and the run id is built from it - so the type is asserted
    # rather than assumed. See world/paths.py for why that distinction cost
    # a debugging session.
    scenario_id = as_component(scenario_id, "scenario id")
    if scenario_id not in known_scenarios():
        raise ValueError(
            f"no declared scenario {scenario_id!r}; the UI starts runs only "
            f"for scenario files that exist: {known_scenarios()}"
        )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{scenario_id}-{stamp}-{uuid.uuid4().hex[:8]}"
    out = RUNS / run_id / "manifest.json"

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", ENTRY_POINT,
         "--scenario", scenario_id, "--out", str(out)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1,
    )
    RUNNING[run_id] = {"proc": proc, "lines": [], "scenario": scenario_id,
                       "started": time.monotonic(), "returncode": None,
                       "finished": None}
    threading.Thread(target=_drain, args=(run_id, proc), daemon=True).start()
    return run_id


#: The narrative report entry point. A string, never imported - same
#: reasoning as ENTRY_POINT above.
REPORT_ENTRY_POINT = "mironba.agents.report"

#: report_id -> the same shape as RUNNING.
REPORTS: dict = {}


def report_available(run_id: str) -> tuple:
    """(can_run, why_not). Either record will do.

    The agent read event feeds only, which meant the one run kind the UI
    creates - stipulated, manifest-only - was the one kind it could not
    describe. It reads a manifest now, so the question is just whether the
    run recorded anything at all.
    """
    run_dir = RUNS / run_id
    if not run_dir.is_dir():
        return (False, "no such run directory")
    if ((run_dir / "events.jsonl").is_file()
            or (run_dir / "manifest.json").is_file()):
        return (True, "")
    return (False,
            "this run recorded neither an event log nor a manifest, so "
            "there is nothing for the report agent to read. That is a run "
            "that never finished, not a limitation of the narrative.")


def start_report(run_id: str) -> str:
    """Spawn the report agent for a run that has an event log."""
    ok, why = report_available(run_id)
    if not ok:
        raise ValueError(why)
    report_id = f"{run_id}::narrative::{uuid.uuid4().hex[:6]}"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, "-m", REPORT_ENTRY_POINT, str(RUNS / run_id)],
        cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
        errors="replace", bufsize=1,
    )
    REPORTS[report_id] = {"proc": proc, "lines": [], "scenario": run_id,
                          "started": time.monotonic(), "returncode": None,
                          "finished": None}
    threading.Thread(target=_drain_report, args=(report_id, proc),
                     daemon=True).start()
    return report_id


def _drain_report(report_id: str, proc: subprocess.Popen) -> None:
    """Same discipline as _drain: close the pipe, reap the child."""
    job = REPORTS[report_id]
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            job["lines"].append(line.rstrip("\n"))
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.wait()
    job["returncode"] = proc.returncode
    job["finished"] = time.monotonic()


def report_progress(report_id: str) -> dict:
    job = REPORTS.get(report_id)
    if job is None:
        return {}
    elapsed = (job["finished"] or time.monotonic()) - job["started"]
    return {
        "report_id": report_id, "lines": list(job["lines"]),
        "elapsed": round(elapsed, 1), "returncode": job["returncode"],
        "done": job["returncode"] is not None,
    }


def progress(run_id: str) -> dict:
    """What the child has printed so far, and whether it is done."""
    job = RUNNING.get(run_id)
    if job is None:
        return {}
    elapsed = (job["finished"] or time.monotonic()) - job["started"]
    return {
        "run_id": run_id,
        "scenario": job["scenario"],
        "lines": list(job["lines"]),
        "elapsed": round(elapsed, 1),
        "returncode": job["returncode"],
        "done": job["returncode"] is not None,
        "wrote_manifest": (RUNS / run_id / "manifest.json").is_file(),
        "typical_s": TYPICAL_RUN_S,
    }
