"""Pre-run the demo scenarios, so a first look costs no wait.

    python -m mironba.report.demos          # regenerates demos/*.json

Drafting a scenario from a sentence calls a local 27B model and takes a
measured p50 of 3.2 minutes. Nobody waits that for a first look, so the
examples on the landing page point at results that are already recorded.

**What is pre-run, and what that costs in fidelity.** The model's job in the
authoring flow is to turn an English sentence into a structured proposal -
which KIND of event it is, and who moves where. Everything that follows is
deterministic: name resolution, ``rules/solver.py`` enumerating legal trade
returns, ``rules/signing.py`` enumerating legal signing routes, and
``rules/trade_validator.py`` ruling on a package. These demos skip the model
by stating the structured proposal directly and then run the identical
deterministic path, so the verdicts, the packages, the routes and the dollar
figures are produced by the same code the live flow uses - not transcribed
from a previous run.

That skipped step is not a formality. The event classification folded into
the full proposal schema answered "trade" on every sentence measured,
including explicit signings, so a demo that stated it for the model is
demonstrating strictly less than the live page does.

That is also the honest limit of the demo: it does NOT demonstrate that the
model extracts this structure correctly from the sentence. It demonstrates
what the rules do with the structure. The live authoring page is where the
extraction step is exercised, and it says so.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMOS = ROOT / "demos"

SEED_DATE = "2026-07-06"

#: Each demo is labelled by WHAT IT SHOWS, not by the players it names.
DEMOS_DECLARED = (
    {
        "name": "solver-enumeration",
        "shows": "8 legal return packages",
        "headline": "The sentence names no return, so the solver builds every "
                    "legal one",
        "sentence": "Victor Wembanyama traded to the Warriors",
        "moves": [{"player_name": "Victor Wembanyama", "from_team": "",
                   "to_team": "Warriors"}],
        "reading": "The model named a player and a destination. It never saw "
                   "a salary. rules/solver.py searched every package Golden "
                   "State could legally send and priced each one; a human "
                   "picks by index. That boundary - model proposes, rules "
                   "dispose - is the one thing this project will not blur.",
    },
    {
        "name": "signing-routes",
        "shows": "a signing, priced by route",
        "headline": "The second kind of seed: a free agent joins a team",
        "sentence": "LeBron James signs with the Golden State Warriors",
        "event": "signing",
        "moves": [{"player_name": "LeBron James", "from_team": "",
                   "to_team": "Warriors"}],
        "reading": "There is no counterparty and no salary matching here, "
                   "so rules/trade_validator.py has nothing to say about "
                   "it. The question rules/ answers instead is whether "
                   "Golden State has a legal ROUTE on the seed date, and "
                   "what each route permits. The sentence states no amount "
                   "and the schema has no field for one: the figures below "
                   "are the solver's, and the run records that they were "
                   "derived rather than declared.",
    },
    {
        "name": "validator-refusal",
        "shows": "the validator refuses this",
        "headline": "A plausible-looking star swap that the rules will not "
                    "allow",
        "sentence": ("Giannis Antetokounmpo traded from MIA to NYK for "
                     "Karl-Anthony Towns"),
        "moves": [
            {"player_name": "Giannis Antetokounmpo", "from_team": "MIA",
             "to_team": "NYK"},
            {"player_name": "Karl-Anthony Towns", "from_team": "NYK",
             "to_team": "MIA"},
        ],
        "reading": "Nothing about this trade looks wrong to a reader, and "
                   "nothing about it looks wrong to a language model either. "
                   "It is refused on arithmetic the 2023 CBA specifies "
                   "exactly, and the shortfall below is quoted from "
                   "rules/trade_validator.py, not paraphrased.",
    },
)


def build(spec: dict) -> dict:
    """Run one demo's deterministic half and record what it produced."""
    from mironba.world.authoring import Draft, validate_draft

    draft = Draft(sentence=spec["sentence"], kind="stipulated",
                  seed_date=SEED_DATE, moves=list(spec["moves"]),
                  event=spec.get("event", "trade"))
    validate_draft(draft)
    return {
        "name": spec["name"],
        "shows": spec["shows"],
        "headline": spec["headline"],
        "reading": spec["reading"],
        "sentence": spec["sentence"],
        "draft": asdict(draft),
        "model": None,
        "model_reason": ("the structured proposal is stated, not extracted; "
                         "everything below it is deterministic"),
        "seed_date": SEED_DATE,
    }


def main(argv=None) -> int:
    DEMOS.mkdir(parents=True, exist_ok=True)
    written = []
    for spec in DEMOS_DECLARED:
        record = build(spec)
        path = DEMOS / f"{spec['name']}.json"
        path.write_text(json.dumps(record, indent=2, default=str),
                        encoding="utf-8")
        draft = record["draft"]
        print(f"  {spec['name']:<20} {len(draft['package_options'])} "
              f"package(s), {len(draft['signing_routes'])} route(s), "
              f"{len(draft['errors'])} error(s), "
              f"{len(draft['findings'])} finding(s)  -> {path.name}")
        written.append(spec["name"])
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "demos": written,
        "model": None,
        "model_reason": "no model call is made; see the module docstring for "
                        "what that means the demo does and does not show",
        "temperature": None,
        "seed": None,
        "seed_reason": "no sampling occurs; the solver enumerates in a fixed "
                       "order",
        "data_snapshot": SEED_DATE,
        "regenerate": "python -m mironba.report.demos",
    }
    (DEMOS / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"  wrote {DEMOS / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
