"""The web UI: presentation only - no new results, no new measurement.

    python -m mironba.api.serve        # one command; Ctrl+C stops everything

**Stack, and the reason:** FastAPI + Jinja2 server-rendered templates with
HTMX for the two interactive steps of the authoring flow. Everything this
UI shows already exists as a committed artifact (runs/, bench json,
evidence stores, docs/figures), so rendering server-side in Python keeps
the honesty constraints testable with the same pytest machinery as every
other surface, and there is no build step to drift. No SPA because there
is no client state worth owning.

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
    })


@app.get("/league", response_class=HTMLResponse)
def league_view(request: Request, run: str | None = None):
    """The league graph: thirty nodes, real edges from a recorded run."""
    from mironba.api.graph import league_graph

    graph = league_graph(run)
    if not graph:
        raise HTTPException(404, "no recorded run carries a reaction and a "
                                 "cascade to draw")
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
    """Watch a run directory as it is written.

    The UI cannot START a run - the import fence keeps sim/ out - so this
    watches what the CLI produces. That is the honest replacement for a
    fixed time estimate: elapsed and event count, measured while it runs.
    """
    runs = sorted((d for d in RUNS.iterdir() if d.is_dir()),
                  key=lambda d: d.stat().st_mtime, reverse=True)[:12]
    rows = [{"id": d.name, "events": (d / "events.jsonl").is_file(),
             "mtime": d.stat().st_mtime} for d in runs]
    return templates.TemplateResponse(request, "live.html", {
        "rows": rows, "llm_label": LLM_PATH_LABEL})


@app.get("/live/{run_id}", response_class=HTMLResponse)
def live_run(request: Request, run_id: str):
    run_dir = RUNS / run_id
    if ".." in run_id or not run_dir.is_dir():
        raise HTTPException(404)
    return templates.TemplateResponse(request, "live_run.html", {
        "run_id": run_id, "manifest": _manifest(run_dir),
        "llm_label": LLM_PATH_LABEL})


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


@app.get("/authoring", response_class=HTMLResponse)
def authoring_page(request: Request):
    return templates.TemplateResponse(request, "authoring.html", {
        "llm_label": LLM_PATH_LABEL, "latency": measured_latency(),
    })


def _validated(draft):
    from mironba.world.authoring import validate_draft

    draft.errors.clear()
    draft.findings.clear()
    return validate_draft(draft)


@app.post("/authoring/draft", response_class=HTMLResponse)
def authoring_draft(request: Request, sentence: str = Form(...)):
    """Step 1 of 2: structure. If the movements call is still needed, the
    fragment auto-continues to /authoring/complete so the progress the user
    sees matches the calls actually being made."""
    from mironba.world.authoring import (_drafting_client,
                                         draft_from_sentence,
                                         needs_moves_call)

    try:
        draft = draft_from_sentence(sentence, _drafting_client())
    except Exception as exc:  # noqa: BLE001 - a down model is a visible state
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": repr(exc)})
    if needs_moves_call(draft):
        return templates.TemplateResponse(request, "_draft_step2.html", {
            "draft": draft, "draft_json": json.dumps(asdict(draft))})
    draft = _validated(draft)
    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft))})


@app.post("/authoring/complete", response_class=HTMLResponse)
async def authoring_complete(request: Request):
    """Step 2 of 2: the tiny movements-only call, then validation."""
    from mironba.world.authoring import Draft, _drafting_client, complete_moves

    form = await request.form()
    draft = Draft(**json.loads(form["draft_json"]))
    try:
        draft = _validated(complete_moves(draft, _drafting_client()))
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": repr(exc)})
    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft))})


@app.post("/authoring/package", response_class=HTMLResponse)
async def authoring_package(request: Request):
    """A human picks one of the solver's legal return packages by index.

    The model never saw a salary: it named a player, rules/solver.py priced
    every legal return, and the choice is yours - the same boundary as the
    trade-intent loop.
    """
    from mironba.world.authoring import AuthoringError, Draft, choose_package

    form = await request.form()
    draft = Draft(**json.loads(form["draft_json"]))
    try:
        choose_package(draft, int(form["package"]))
    except (AuthoringError, ValueError, KeyError) as exc:
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc)})
    draft = _validated(draft)
    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft))})


@app.post("/authoring/resolve", response_class=HTMLResponse)
async def authoring_resolve(request: Request):
    from mironba.world.authoring import Draft, choose

    form = await request.form()
    draft = Draft(**json.loads(form["draft_json"]))
    for key, value in form.items():
        if key.startswith("choose:") and value:
            choose(draft, key.split(":", 1)[1], str(value))
    draft = _validated(draft)
    return templates.TemplateResponse(request, "_draft.html", {
        "draft": draft, "draft_json": json.dumps(asdict(draft))})


@app.post("/authoring/write", response_class=HTMLResponse)
async def authoring_write(request: Request):
    """THE GATE. write_scenario(confirmed=True) is reached only from the
    explicit checkbox; anything else is 400 and nothing touches disk."""
    from mironba.world.authoring import AuthoringError, Draft, write_scenario

    form = await request.form()
    if form.get("confirmed") != "yes":
        raise HTTPException(400, "confirmation is a human act: check the "
                                 "confirm box; nothing was written")
    scenario_id = str(form.get("scenario_id", "")).strip()
    if not scenario_id:
        raise HTTPException(400, "a scenario id is required; nothing was written")
    draft = Draft(**json.loads(form["draft_json"]))
    draft = _validated(draft)
    try:
        path = write_scenario(draft, scenario_id, confirmed=True)
    except AuthoringError as exc:
        return templates.TemplateResponse(request, "_draft_error.html", {
            "error": str(exc)})
    return HTMLResponse(f"<div class='ok'>wrote {path.name} - confirmed "
                        "explicitly by you, the same gate as the CLI</div>")


# -- (f) run gallery and (b) run view ---------------------------------------


@app.get("/runs", response_class=HTMLResponse)
def run_gallery(request: Request):
    rows = []
    # newest first BY TIME, not by name - reverse-alphabetical put one run
    # family's ~60 dirs on the whole first page and hid every badge
    run_dirs = sorted((d for d in RUNS.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
    for run_dir in run_dirs:
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
    return templates.TemplateResponse(request, "run.html", {
        "run_id": run_id, "manifest": manifest, "feed": feed,
        "unfalsifiable": manifest.get("unfalsifiable", False),
        "llm_label": LLM_PATH_LABEL})


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


# -- (d) report ----------------------------------------------------------------


@app.get("/report", response_class=HTMLResponse)
def report_view(request: Request):
    from mironba.agents.report import LIMITATIONS

    report_runs = sorted(RUNS.glob("report-*"), reverse=True)
    text, source = "", ""
    for run_dir in report_runs:
        calls = run_dir / "llm_calls.jsonl"
        if not calls.is_file():
            continue
        for line in reversed(calls.read_text(encoding="utf-8").splitlines()):
            row = json.loads(line)
            if row.get("ok") and row.get("response_text"):
                text, source = row["response_text"], run_dir.name
                break
        if text:
            break
    return templates.TemplateResponse(request, "report.html", {
        "text": text, "source": source, "limitations": LIMITATIONS,
        "llm_label": LLM_PATH_LABEL})


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
