"""The web UI: presentation only - no new results, no new measurement.

    python -m mironba.api.serve        # one command; Ctrl+C stops everything

**Stack, and the reason:** FastAPI + Jinja2 server-rendered templates with
HTMX for the two interactive steps of the authoring flow. Everything this
UI shows already exists as a committed artifact (runs/, bench json,
evidence stores, docs/figures), so rendering server-side in Python keeps
the honesty constraints testable with the same pytest machinery as every
other surface, and there is no build step to drift. No SPA because there
is no client state worth owning.

**What it starts, and what it does not compute:** the Run button spawns the
simulation CLI as a subprocess and then reads the manifest that child wrote.
``mironba/api/runner.py`` states why that preserves the fence instead of
evading it, and a test checks the property that matters - after a run is
started from here, ``mironba.sim`` is still absent from this process.

**What it reads:** committed artifacts only. The import fence test keeps
``mironba.sim``, ``mironba.models`` and ``mironba.eval`` out of this
package entirely - a UI that can reach the simulation can quietly become a
second results pipeline. The one LLM call in the whole surface is the
authoring draft (screen a), which is the CLI flow this screen exists to
mirror; its confirm gate stays ``write_scenario(confirmed=True)``, reached
only from an explicit form field.

**Honesty constraints, each enforced by test:** no observed value without
its null; POST-freeze evidence never renders; reported interest is
labelled an input; the LLM path is labelled one-model-per-tick; a run's
UNFALSIFIABLE flag renders as a header, not a footnote.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
FIGURES = ROOT / "docs" / "figures"

app = FastAPI(title="MiroNBA", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/figures", StaticFiles(directory=str(FIGURES)), name="figures")

#: The label every page carries (tested): the intent loop is ONE model per
#: tick - the thirty teams are deterministic code, not thirty LLMs.
LLM_PATH_LABEL = ("LLM path: one model per tick in the intent loop - "
                  "not thirty agents. The thirty-team reaction is "
                  "deterministic code.")

#: The boundary, in the charter's own words followed by what they mean
#: mechanically. It used to sit in the landing hero, where a first-time
#: reader met "only rules/ may approve" before anything had told them
#: rules/ was a directory. It belongs where the architecture is explained.
BOUNDARY_CLAIM = (
    "An LLM may propose; only rules/ may approve. The model names players "
    "and selects from a list - it never emits a package, never states "
    "terms, and nothing it says reaches world state without passing the "
    "validator."
)

#: Three sentences, each demonstrating a DIFFERENT behaviour of the flow -
#: labelled by what they demonstrate, not by their content. The third is
#: the distinctive one: a refusal, with the shortfall quoted from rules/.
EXAMPLES = (
    {"demonstrates": "Under-specified — the solver builds the return",
     "shows": "8 legal return packages", "demo": "/demo/solver-enumeration",
     "sentence": "Victor Wembanyama traded to the Warriors",
     "expect": "names an incoming player and no return, so rules/solver.py "
               "enumerates every legal package GSW could send (8 of them) "
               "and you pick one."},
    {"demonstrates": "Fully specified — validates LEGAL",
     "shows": "seed causes 4 of 9 trades", "demo": "/runs/curry-lakers-2026",
     "sentence": ("Stephen Curry traded from GSW to LAL for Austin Reaves "
                  "and Quentin Grimes on 2026-07-06"),
     "expect": "both sides named; the validator approves it and still "
               "reports its findings (LAL ends hard-capped at the first "
               "apron; GSW drops below the roster minimum)."},
    {"demonstrates": "A signing, not a trade — priced by route",
     "shows": "a signing, priced by route", "demo": "/demo/signing-routes",
     "sentence": "LeBron James signs with the Golden State Warriors",
     "expect": "no counterparty and no salary matching, so rules/signing.py "
               "answers instead: every legal route the destination has, and "
               "the maximum under each. The amount is never stated."},
    {"demonstrates": "REFUSED — the rules say no, with the shortfall",
     "shows": "the validator refuses this",
     "demo": "/demo/validator-refusal",
     "sentence": ("Giannis Antetokounmpo traded from MIA to NYK for "
                  "Karl-Anthony Towns"),
     "expect": "a plausible-looking star swap that is illegal: New York "
               "takes back more than 100% apron matching allows, and the "
               "page quotes the exact dollar shortfall from rules/."},
)

#: Figure captions: filename -> (title, the null it is read against).
FIGURE_CAPTIONS = (
    ("three-arm.svg", "Three-arm A/B",
     "each arm is the others' control; stand-pat rates 19.4/19.4/20.7% are "
     "the null against attempting less"),
    ("metrics-vs-nulls.svg", "Every metric against its null",
     "the null marker sits on every row; failures included by test"),
    ("correction-chain.svg", "The correction chain",
     "class-balance null 6.3% and within-team null 7.0% drawn on the figure"),
    ("deadline-per-season.svg", "Deadline recall per season",
     "each season against its OWN null; two fall below"),
    ("persistence-power.svg", "Persona persistence, n=11 vs n=29",
     "the coin-flip null is the dashed line; nothing cleared p=0.05"),
)



def run_dirs() -> list:
    """Run directories, newest first. Empty when runs/ does not exist.

    A fresh clone has no runs/ at all - it is gitignored - and
    ``RUNS.iterdir()`` raised FileNotFoundError, so the LANDING PAGE, the
    gallery, the live index and the league graph all returned 500 on a
    clone. Four of the eight routes a first visitor can reach, and the
    front door among them. An absent directory is a legitimate state here,
    not an error: it means nothing has been run yet, which is exactly what
    every one of those pages now says.
    """
    if not RUNS.is_dir():
        return []
    return sorted((d for d in RUNS.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(run_dir: Path) -> dict:
    path = run_dir / "manifest.json"
    return _read_json(path) if path.is_file() else {}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    """The front door: one line on what this is, the boundary finding as
    the headline claim, a hero animated from a real run's cascade, and the
    way in to authoring."""
    from mironba.api.graph import headline_numbers, hero_frames

    return templates.TemplateResponse(request, "index.html", {
        "llm_label": LLM_PATH_LABEL,
        "hero": hero_frames(limit=9),
        "numbers": headline_numbers()[:2],
        "examples": EXAMPLES,
        "latency": measured_latency(),
        "boundary": BOUNDARY_CLAIM,
    })


@app.get("/league", response_class=HTMLResponse)
def league_view(request: Request, run: str | None = None):
    """The league graph: thirty nodes, real edges from a recorded run."""
    from mironba.api.graph import league_graph

    graph = league_graph(run)
    if not graph:
        # NOT a 404. There is nothing wrong with the request - there is
        # simply no run to draw yet, which is the ordinary state of a fresh
        # clone, and a 404 tells the reader they mistyped something.
        return templates.TemplateResponse(request, "league.html", {
            "g": {}, "llm_label": LLM_PATH_LABEL, "unfalsifiable": False})
    # /league is the newest run by default. Per-run graphs live at
    # /runs/{id}/league and are linked from each run view, so a freshly
    # authored scenario does not have to become the newest demo to be seen.
    return templates.TemplateResponse(request, "league.html", {
        "g": graph, "llm_label": LLM_PATH_LABEL,
        "unfalsifiable": graph["unfalsifiable"]})


def _events_after(run_dir: Path, after: int) -> list:
    path = run_dir / "events.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 - a half-written line is not an event
            continue
        if int(row.get("seq", 0)) > after:
            rows.append(row)
    return rows


@app.get("/live", response_class=HTMLResponse)
def live_index(request: Request):
    """Watch a run, and start one.

    The UI starts a run by SPAWNING the CLI, never by importing it: see
    mironba/api/runner.py for why that keeps the fence intact. Either way
    what this page shows is observation - the child's own stdout, or the
    event log the CLI wrote - with elapsed measured, never estimated.
    """
    from mironba.api import runner

    runs = run_dirs()[:12]
    rows = [{"id": d.name, "events": (d / "events.jsonl").is_file(),
             "mtime": d.stat().st_mtime} for d in runs]
    return templates.TemplateResponse(request, "live.html", {
        "rows": rows, "scenarios": runner.known_scenarios(),
        "typical_s": runner.TYPICAL_RUN_S, "llm_label": LLM_PATH_LABEL})


@app.get("/live/{run_id}", response_class=HTMLResponse)
def live_run(request: Request, run_id: str):
    """Watch one run.

    A run this UI started is watched by reading the child process's stdout;
    a run started in a terminal is watched by tailing its event log. Both
    are observation - neither computes anything here.
    """
    from mironba.api import runner

    progress = runner.progress(run_id)
    run_dir = RUNS / run_id
    if ".." in run_id or (not progress and not run_dir.is_dir()):
        raise HTTPException(404)
    return templates.TemplateResponse(request, "live_run.html", {
        "run_id": run_id, "manifest": _manifest(run_dir),
        "progress": progress, "llm_label": LLM_PATH_LABEL})


@app.get("/live/{run_id}/events", response_class=HTMLResponse)
def live_events(request: Request, run_id: str, after: int = 0):
    """One polling step: the events produced since ``after``."""
    import time

    run_dir = RUNS / run_id
    if ".." in run_id or not run_dir.is_dir():
        raise HTTPException(404)
    rows = _events_after(run_dir, after)
    path = run_dir / "events.jsonl"
    last_write = path.stat().st_mtime if path.is_file() else 0
    started = _manifest(run_dir).get("started_at", "")
    highest = after
    entries = []
    for row in rows:
        highest = max(highest, int(row.get("seq", 0)))
        entries.append({
            "seq": row.get("seq"), "kind": row.get("kind", ""),
            "ts": str(row.get("ts", ""))[11:19],
            "detail": json.dumps({k: v for k, v in row.items()
                                  if k not in ("seq", "ts", "kind",
                                               "run_id")})[:160],
        })
    return templates.TemplateResponse(request, "_live_events.html", {
        "entries": entries, "after": highest, "run_id": run_id,
        "idle_s": round(time.time() - last_write) if last_write else None,
        "started": started})


# -- (a) scenario input -----------------------------------------------------


def measured_latency() -> dict:
    """The MEASURED draft latency from the recorded bench - never an
    estimate typed into a template (the UI once promised ~30s and took far
    longer; item 3 of the fix brief)."""
    path = ROOT / "bench-authoring-latency.json"
    if not path.is_file():
        return {}
    bench = _read_json(path)
    return {"p50": bench.get("p50_s"), "worst": bench.get("worst_s"),
            "n": bench.get("n"), "warm": bench.get("warm_p50_s")}


#: In-flight drafting jobs. A draft takes minutes on a local model, so the
#: request that starts one returns IMMEDIATELY with a polling view and the
#: work continues on a thread - the user watches it happen instead of
#: staring at a spinner wondering whether it hung.
JOBS: dict = {}


def _draft_job(job_id: str, sentence: str) -> None:
    import time
    import traceback

    from mironba.world.authoring import (_drafting_client, complete_event,
                                         complete_moves, draft_from_sentence,
                                         needs_event_call, needs_moves_call,
                                         validate_draft)

    job = JOBS[job_id]
    start = time.monotonic()

    def mark(name: str, detail: str = "") -> None:
        job["steps"].append({"name": name, "detail": detail,
                             "at": round(time.monotonic() - start, 1)})

    try:
        mark("model ready", "local 27B, thinking on")
        client = _drafting_client()
        draft = draft_from_sentence(sentence, client)
        mark("step 1/2: structure extracted",
             f"kind={draft.kind} · players "
             f"{', '.join(draft.player_names) or 'none'} · teams "
             f"{', '.join(draft.team_codes) or 'none'}")
        if needs_event_call(draft):
            draft = complete_event(draft, client)
            mark(f"event classified: {draft.event}",
                 "asked on its own with a one-field schema - folded into "
                 "the full proposal this field answered 'trade' on every "
                 "sentence measured, including explicit signings")
        if needs_moves_call(draft):
            mark("step 2/2: the one-shot returned no movements",
                 "asking again with the tiny movements-only schema - the "
                 "charter's documented failure mode for this model")
            draft = complete_moves(draft, client)
            mark("step 2/2: movements extracted",
                 "; ".join(f"{m['player_name']} -> {m['to_team']}"
                           for m in draft.moves) or "still none")
        validate_draft(draft, on_step=mark)
        job["draft"] = draft
    except Exception as exc:  # noqa: BLE001 - a failed draft is a visible state
        job["error"] = repr(exc)
        job["trace"] = traceback.format_exc()[-400:]
    finally:
        job["done"] = True
        job["elapsed"] = round(time.monotonic() - start, 1)


@app.get("/authoring", response_class=HTMLResponse)
def authoring_page(request: Request):
    return templates.TemplateResponse(request, "authoring.html", {
        "llm_label": LLM_PATH_LABEL, "latency": measured_latency(),
        "examples": EXAMPLES,
    })





def _limitations() -> tuple:
    """The standing limitations, read from the agent that owns them.

    They lived on /report, a page one link deep that showed one arbitrary
    artifact. Moving them onto the run view puts them on the page every run
    lands on - which is what "structurally undismissable" has to mean if it
    means anything. Imported rather than copied so there is one list.
    """
    from mironba.agents.report import LIMITATIONS

    return LIMITATIONS


def _draft_from_form(form) -> "object":
    """Rebuild the Draft a form is carrying, or 400 with the reason.

    Three handlers do this and all three used to construct it bare, so a
    malformed or truncated payload surfaced as an unhandled TypeError and a
    500. A bad request is the client's to fix and should say so; a 500 says
    the server broke, which is a different claim.
    """
    from mironba.world.authoring import Draft

    raw = form.get("draft_json")
    if not raw:
        raise HTTPException(400, "the form carried no draft; nothing was "
                                 "written. Draft the sentence again.")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            400, f"the draft payload is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "the draft payload is not an object")
    try:
        return Draft(**payload)
    except TypeError as exc:
        raise HTTPException(
            400, f"the draft payload does not describe a draft: {exc}. "
                 "Nothing was written.") from exc


def _derived_id(draft) -> str:
    """The id this draft would be written under, for display and default."""
    from mironba.world.authoring import (derive_scenario_id,
                                         existing_scenario_ids)

    try:
        return derive_scenario_id(draft, taken=existing_scenario_ids())
    except Exception:  # noqa: BLE001 - a naming failure must not lose a draft
        return ""


def _validated(draft):
    from mironba.world.authoring import validate_draft

    draft.errors.clear()
    draft.findings.clear()
    return validate_draft(draft)


@app.post("/authoring/draft", response_class=HTMLResponse)
def authoring_draft(request: Request, sentence: str = Form(...)):
    """Start a drafting job and return the watcher IMMEDIATELY.

    The work happens on a thread; the fragment returned here polls
    /authoring/job/{id} once a second and shows each step as it lands.
    """
    import threading
    import uuid

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"sentence": sentence, "steps": [], "done": False,
                    "draft": None, "error": None}
    threading.Thread(target=_draft_job, args=(job_id, sentence),
                     daemon=True).start()
    return templates.TemplateResponse(request, "_job.html", {
        "job_id": job_id, "job": JOBS[job_id],
        "latency": measured_latency()})


@app.get("/authoring/job/{job_id}", response_class=HTMLResponse)
def authoring_job(request: Request, job_id: str):
    """One polling step of a drafting job: the steps that have landed."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such drafting job")
    if job["done"] and job["error"]:
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": job["error"]})
    if job["done"] and job["draft"] is not None:
        draft = job["draft"]
        from mironba.api import runner

        return templates.TemplateResponse(request, "_draft.html", {
            "draft": draft, "draft_json": json.dumps(asdict(draft)),
            "elapsed": job.get("elapsed"), "steps": job["steps"],
            "derived_id": _derived_id(draft),
            "typical_s": runner.TYPICAL_RUN_S})
    return templates.TemplateResponse(request, "_job.html", {
        "job_id": job_id, "job": job, "latency": measured_latency()})


@app.post("/authoring/package", response_class=HTMLResponse)
async def authoring_package(request: Request):
    """A human picks one of the solver's legal return packages by index.

    The model never saw a salary: it named a player, rules/solver.py priced
    every legal return, and the choice is yours - the same boundary as the
    trade-intent loop.
    """
    from mironba.world.authoring import AuthoringError, Draft, choose_package

    form = await request.form()
    draft = _draft_from_form(form)
    try:
        choose_package(draft, int(form["package"]))
    except (AuthoringError, ValueError, KeyError) as exc:
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc)})
    draft = _validated(draft)
    from mironba.api import runner

    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft)),
        "derived_id": _derived_id(draft),
        "typical_s": runner.TYPICAL_RUN_S})


@app.post("/authoring/resolve", response_class=HTMLResponse)
async def authoring_resolve(request: Request):
    from mironba.world.authoring import Draft, choose

    form = await request.form()
    draft = _draft_from_form(form)
    for key, value in form.items():
        if key.startswith("choose:") and value:
            choose(draft, key.split(":", 1)[1], str(value))
    draft = _validated(draft)
    from mironba.api import runner

    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft)),
        "derived_id": _derived_id(draft),
        "typical_s": runner.TYPICAL_RUN_S})


@app.post("/authoring/write", response_class=HTMLResponse)
async def authoring_write(request: Request):
    """THE GATE. write_scenario(confirmed=True) is reached only from the
    explicit checkbox; anything else is 400 and nothing touches disk."""
    from mironba.world.authoring import (AuthoringCrash, AuthoringError,
                                         Draft, write_scenario)

    form = await request.form()
    if form.get("confirmed") != "yes":
        raise HTTPException(400, "confirmation is a human act: check the "
                                 "confirm box; nothing was written")
    draft = _draft_from_form(form)
    draft = _validated(draft)
    # DERIVED, not demanded. The id is a function of what resolved, so a
    # user who never types one still gets a stable, readable, non-numeric
    # name - and str() is not decorative here: a numeric id is what crashed
    # the loader on a path join.
    scenario_id = str(form.get("scenario_id", "") or "").strip()
    if not scenario_id:
        scenario_id = _derived_id(draft)
    if not scenario_id:
        raise HTTPException(
            400, "no scenario id could be derived from this draft and none "
                 "was supplied; nothing was written")
    try:
        path = write_scenario(draft, scenario_id, confirmed=True)
    except AuthoringError as exc:
        # A verdict: the scenario was judged and rejected.
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc)})
    except AuthoringCrash as exc:
        # NOT a verdict. Saying "your scenario is invalid" here would be a
        # claim nobody made - the check never finished.
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc), "crash": True,
            "traceback": getattr(exc, "traceback", "")})
    from mironba.api import runner

    # WRITE AND RUN, as one action. The confirm checkbox is the gate on
    # writing, and it has just been passed; making the user find a second
    # button afterwards is how a run gets lost. A user who wants the file
    # without the reaction says so with the other button.
    if str(form.get("run", "yes")) == "yes":
        try:
            run_id = runner.start(scenario_id)
        except ValueError as exc:
            return templates.TemplateResponse(request, "_written.html", {
                "path": path.name, "scenario_id": scenario_id,
                "typical_s": runner.TYPICAL_RUN_S, "start_error": str(exc)})
        response = templates.TemplateResponse(request, "_run_progress.html", {
            "p": runner.progress(run_id)})
        response.headers["HX-Redirect"] = f"/live/{run_id}"
        return response
    return templates.TemplateResponse(request, "_written.html", {
        "path": path.name, "scenario_id": scenario_id,
        "typical_s": runner.TYPICAL_RUN_S})



# -- pre-run demos: a first look costs no wait -------------------------------

DEMOS = ROOT / "demos"


@app.get("/demo/{name}", response_class=HTMLResponse)
def demo_view(request: Request, name: str):
    """One pre-run demo, read from a committed artifact.

    Drafting live calls a 27B model and takes minutes; these skip the
    extraction step and record what the DETERMINISTIC half produced. The
    page says which half it is showing - see mironba/report/demos.py.
    """
    path = DEMOS / f"{name}.json"
    if "/" in name or ".." in name or not path.is_file():
        raise HTTPException(404, "no such pre-run demo")
    record = _read_json(path)
    generated = _read_json(DEMOS / "manifest.json").get("generated_at", "")
    return templates.TemplateResponse(request, "demo.html", {
        "d": record, "draft": record["draft"], "generated": generated,
        "llm_label": LLM_PATH_LABEL})


# -- starting a run: a subprocess, never an import ---------------------------


@app.post("/runs/start", response_class=HTMLResponse)
def runs_start(request: Request, scenario_id: str = Form(...)):
    """Start the CLI for a written scenario, as a SUBPROCESS.

    See mironba/api/runner.py for why this preserves the import fence rather
    than working around it: the computation stays in sim/, and everything
    this UI shows afterwards is read back off the manifest the child wrote.
    """
    from mironba.api import runner

    try:
        run_id = runner.start(scenario_id)
    except ValueError as exc:
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc)})
    response = templates.TemplateResponse(request, "_run_progress.html", {
        "p": runner.progress(run_id)})
    response.headers["HX-Redirect"] = f"/live/{run_id}"
    return response


@app.get("/runs/progress/{run_id}", response_class=HTMLResponse)
def runs_progress(request: Request, run_id: str):
    """One polling step of a started run: the child's stdout so far.

    On a clean exit this answers with HX-Redirect, which lands the user on
    that run's own view - the cascade it produced - rather than back on a
    list of run directories.
    """
    from mironba.api import runner

    progress = runner.progress(run_id)
    if not progress:
        raise HTTPException(404, "no such started run")
    response = templates.TemplateResponse(request, "_run_progress.html", {
        "p": progress})
    if progress["done"] and progress["returncode"] == 0:
        response.headers["HX-Redirect"] = f"/runs/{run_id}"
    return response

# -- (f) run gallery and (b) run view ---------------------------------------


@app.get("/runs", response_class=HTMLResponse)
def run_gallery(request: Request):
    rows = []
    # newest first BY TIME, not by name - reverse-alphabetical put one run
    # family's ~60 dirs on the whole first page and hid every badge
    for run_dir in run_dirs():
        m = _manifest(run_dir)
        rows.append({
            "id": run_dir.name,
            "model": m.get("model_id") or m.get("model") or "none (deterministic)",
            "seed": m.get("seed"),
            "snapshot": m.get("snapshot_date") or m.get("data_snapshot", ""),
            "gpu_fraction": m.get("gpu_fraction"),
            "reproducible": m.get("reproducible"),
            "unfalsifiable": m.get("unfalsifiable", False),
            "scenario": m.get("scenario_id") or m.get("scenario", ""),
            "has_events": (run_dir / "events.jsonl").is_file(),
            "has_manifest": bool(m),
        })
    return templates.TemplateResponse(request, "runs.html", {
        "rows": rows[:60], "total": len(rows), "llm_label": LLM_PATH_LABEL})


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_view(request: Request, run_id: str):
    run_dir = RUNS / run_id
    if not run_dir.is_dir() or ".." in run_id:
        raise HTTPException(404)
    manifest = _manifest(run_dir)
    feed = None
    if (run_dir / "events.jsonl").is_file():
        from mironba.report.timeline import load_run

        feed = load_run(run_dir)
    from mironba.api.detail import detail_report
    from mironba.api.graph import (cascade_payoff, obligations_view,
                                   pursuit_view, run_graph, signing_view)

    return templates.TemplateResponse(request, "run.html", {
        "run_id": run_id, "manifest": manifest, "feed": feed,
        "g": run_graph(manifest, run_id),
        "payoff": cascade_payoff(manifest),
        "detail": detail_report(manifest),
        "limitations": _limitations(),
        "narrative_ok": __import__(
            "mironba.api.runner", fromlist=["x"]).report_available(run_id),
        "duties": obligations_view(manifest),
        "pursuit": pursuit_view(manifest),
        "signing": signing_view(manifest),
        "trade": manifest.get("trade") or {},
        "unfalsifiable": manifest.get("unfalsifiable", False),
        "llm_label": LLM_PATH_LABEL})



@app.get("/runs/{run_id}/league", response_class=HTMLResponse)
def run_league(request: Request, run_id: str):
    """The graph for ONE run, full width.

    The same figure the run view embeds - not a second implementation.
    Every completed run has one, including a scenario authored a minute
    ago; the graph is a view of a manifest, not a property of the two
    recorded demos.
    """
    from mironba.api.graph import run_graph

    run_dir = RUNS / run_id
    if not run_dir.is_dir() or ".." in run_id:
        raise HTTPException(404)
    manifest = _manifest(run_dir)
    graph = run_graph(manifest, run_id)
    if not graph:
        return templates.TemplateResponse(request, "league.html", {
            "g": {}, "run_id": run_id, "llm_label": LLM_PATH_LABEL,
            "unfalsifiable": False,
            "no_reaction": True})
    return templates.TemplateResponse(request, "league.html", {
        "g": graph, "llm_label": LLM_PATH_LABEL,
        "unfalsifiable": graph["unfalsifiable"]})



@app.post("/runs/{run_id}/narrative", response_class=HTMLResponse)
def run_narrative(request: Request, run_id: str):
    """Start the narrative report. NEVER on the path of the run's output.

    The deterministic detail report is already on screen by the time this
    can be clicked; this adds prose and nothing else, so it is an explicit
    extra with its cost stated rather than something the run waits on.
    """
    from mironba.api import runner

    if ".." in run_id or not (RUNS / run_id).is_dir():
        raise HTTPException(404)
    try:
        report_id = runner.start_report(run_id)
    except ValueError as exc:
        return templates.TemplateResponse(request, "_narrative.html", {
            "unavailable": str(exc), "run_id": run_id})
    return templates.TemplateResponse(request, "_narrative.html", {
        "p": runner.report_progress(report_id), "run_id": run_id})


@app.get("/runs/{run_id}/narrative/{report_id}", response_class=HTMLResponse)
def run_narrative_progress(request: Request, run_id: str, report_id: str):
    from mironba.api import runner

    progress = runner.report_progress(report_id)
    if not progress:
        raise HTTPException(404, "no such report job")
    return templates.TemplateResponse(request, "_narrative.html", {
        "p": progress, "run_id": run_id})


# -- (c) branch comparison ---------------------------------------------------


@app.get("/branches/{scenario_id}", response_class=HTMLResponse)
def branch_view(request: Request, scenario_id: str):
    path = ROOT / f"branch-{scenario_id}.json"
    if not path.is_file() or "/" in scenario_id:
        raise HTTPException(404)
    data = _read_json(path)
    from mironba.report.evidence_view import (INPUT_MARKER, known_at_freeze,
                                              load_scenario_ledger)

    ledger = load_scenario_ledger(scenario_id)
    conditionals = {c.id: c for c in ledger.open_conditionals()} if ledger else {}
    interest = known_at_freeze(ledger) if ledger else []
    branches = []
    for branch in data["branches"]:
        fired = set(branch.get("commitments", []))
        branches.append({
            "label": branch["label"],
            "falsifiable": branch.get("falsifiable", False),
            "moves": branch.get("moves", []),
            "committed_after": branch.get("committed_after"),
            "conditionals": [
                {"id": cid, "condition": c.condition, "commitment": c.commitment,
                 "fired": cid in fired}
                for cid, c in sorted(conditionals.items())
            ],
        })
    return templates.TemplateResponse(request, "branch.html", {
        "scenario_id": scenario_id, "branches": branches,
        "freeze": data.get("freeze", ""), "interest": interest,
        "input_marker": INPUT_MARKER, "llm_label": LLM_PATH_LABEL})


# -- (d) report: RETIRED -----------------------------------------------------
#
# /report displayed one artifact chosen by globbing runs/report-* by NAME and
# taking the newest with any successful completion in it. It never looked at
# what the completion WAS. Two of the seven report-* directories contain only
# agent_chat calls, the newest was one of them, and the page therefore showed
# a GM's chat answer - '{"answer": "I declined the package because..."}',
# raw JSON - under the heading "recorded output of the report agent".
#
# The shape was wrong as well as the selection. A report belongs to a run;
# one global page for it can only ever show an arbitrary one. So the prose
# moved to where the run is (POST /runs/{id}/narrative, optional, below a
# deterministic report already on screen), and LIMITATIONS moved into the
# detail report every run renders - structurally undismissable there in a way
# a page nobody visits never was.
#
# The recorded artifacts are untouched under runs/report-*.

# -- (e) results --------------------------------------------------------------


@app.get("/results", response_class=HTMLResponse)
def results_view(request: Request):
    from mironba.api.graph import headline_numbers, season_series, spark_path

    figures = [{"file": name, "title": title, "null": null}
               for name, title, null in FIGURE_CAPTIONS
               if (FIGURES / name).is_file()]
    sparks = []
    for series in season_series():
        sparks.append(dict(series,
                           path=spark_path(series["points"]),
                           null_path=spark_path(series["nulls"])
                           if series["nulls"] else ""))
    return templates.TemplateResponse(request, "results.html", {
        "figures": figures, "numbers": headline_numbers(), "sparks": sparks,
        "llm_label": LLM_PATH_LABEL})
