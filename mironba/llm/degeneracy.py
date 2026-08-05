"""Which model-filled label fields have only ever emitted one value.

    python -m mironba.llm.degeneracy          # scan runs/, write the record

A field that can only return one of its two values is not a weak classifier,
it is a constant - and unlike accuracy, that is visible **from the outputs
alone**. No labels, no held-out set, no 12-sentence study. Every
``runs/*/llm_calls.jsonl`` already records what the model emitted; this reads
them and counts.

That matters because of what #77 established. `event` was inert inside the
proposal schema and `kind`, the identically-shaped field beside it, was
flawless there. Schema size predicts nothing, so an A/B study per field would
be the only sound alternative - hours each, more than the splits could save.
This costs a scan of files that already exist.

**What it cannot do, stated here because a clean distribution invites the
wrong reading.** This finds inert fields. It cannot tell a correct field from
a wrong one. A field emitting both of its values in healthy proportion may be
emitting them for the wrong sentences every time, and nothing here would
notice: there are no labels in a run record, only outputs. Accuracy still
needs a labelled set and a real study with its own null. What changed is only
that *one specific failure* - the constant wearing a classifier's type - no
longer requires one.

**And a single-valued field is not a defect.** It is a candidate for the
study, and only above a stated N. The anchor is concrete: during the #77
measurement, `kind` had emitted nothing but "stipulated" through the first
six sentences - the set was ordered with the stipulated half first - and
looked exactly like a degenerate field. It went on to score 12/12. Six
observations of one value proved nothing at all, because the first six inputs
were all of one kind. The same is true of any field here whose inputs have
not varied.
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parent
RUNS = ROOT / "runs"
RECORD = ROOT / "field-distributions.json"

#: Below this many observations a single-valued field says nothing. Not a
#: round number chosen for comfort: at N=6 the `kind` field looked degenerate
#: mid-measurement and finished 12/12 (#77), so six is demonstrably too few.
#: Twelve matches the study size the flag would send a field to.
MIN_N = 12


@dataclass
class FieldObservation:
    """One Literal field, and every value it has been seen to emit."""

    schema: str
    field: str
    allowed: tuple
    counts: Counter = field(default_factory=Counter)
    runs: set = field(default_factory=set)
    #: Calls whose schema DID list this field. Only these can omit it.
    asked: int = 0
    #: Calls that asked for it and got nothing back. The #74 signature.
    omitted: int = 0
    #: Values outside the Literal. Evidence about ENFORCEMENT, not accuracy.
    invalid: Counter = field(default_factory=Counter)
    #: Calls of this schema seen at all, including ones predating the field.
    calls: int = 0

    @property
    def n(self) -> int:
        return sum(self.counts.values())

    @property
    def values_seen(self) -> list:
        return sorted(self.counts)

    @property
    def never_emitted(self) -> list:
        return sorted(set(self.allowed) - set(self.counts))

    @property
    def single_valued(self) -> bool:
        return len(self.counts) == 1 and len(self.allowed) > 1

    @property
    def flag(self) -> str:
        """What the record claims, which is deliberately little."""
        # ASKED FOR AND NEVER RETURNED is the sharpest signal here and does
        # not depend on the inputs varying at all. A field the model never
        # emits is answered entirely by its pydantic default, whatever the
        # sentence said - which is precisely what #74 turned out to be, and
        # this would have shown it without a single label.
        if self.asked and self.omitted == self.asked:
            if self.asked < MIN_N:
                return (f"OMITTED in all {self.asked} call(s) that asked for "
                        f"it, but that is below the n={MIN_N} floor")
            return (f"OMITTED: asked for in {self.asked} call(s) and returned "
                    "in none of them. The value in use is the pydantic "
                    "default, not an answer. Unlike a single-valued field "
                    "this does NOT depend on the inputs having varied - the "
                    "model was asked every time and answered never. This is "
                    "the #74 signature")
        if not self.counts:
            if self.asked:
                return (f"asked for {self.asked} time(s), no value parsed - "
                        "check the response format before reading anything "
                        "into this")
            return ("UNOBSERVED - no recorded call asked for this field. "
                    "Says nothing about the field; says the scan has no "
                    "data yet")
        if not self.single_valued:
            return "uses more than one value; nothing to flag here"
        if self.n < MIN_N:
            return (f"single-valued at n={self.n}, BELOW the n={MIN_N} floor "
                    "- uninformative. The inputs may simply not have varied, "
                    "which is exactly what made `kind` look degenerate at "
                    "n=6 before it scored 12/12")
        return (f"CANDIDATE FOR STUDY: single-valued across n={self.n} calls "
                f"in {len(self.runs)} run(s), never emitting "
                f"{', '.join(self.never_emitted)}. Not a confirmed defect - "
                "every input so far may genuinely have been of one kind. "
                "The next step is a labelled A/B against its own null, not "
                "a code change")

    def as_dict(self) -> dict:
        return {
            "schema": self.schema, "field": self.field,
            "allowed": list(self.allowed),
            "counts": dict(sorted(self.counts.items())),
            "n": self.n, "runs": len(self.runs), "calls": self.calls,
            "asked": self.asked, "omitted": self.omitted,
            "invalid": dict(sorted(self.invalid.items())),
            "values_seen": self.values_seen,
            "never_emitted": self.never_emitted,
            "single_valued": self.single_valued,
            "flag": self.flag,
        }


def literal_fields() -> dict:
    """(schema, field) -> allowed values, for every Literal in the package.

    Derived by AST, never declared. The schema_audit registry got two of ten
    hand-typed counts wrong; a list of label fields maintained by hand would
    rot the same way, and the rot would be invisible - a field dropped from
    the list simply stops being watched.
    """
    out: dict = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(b, "id", getattr(b, "attr", ""))
                     for b in node.bases}
            if "BaseModel" not in bases:
                continue
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                values = _literal_values(item.annotation)
                if values and isinstance(item.target, ast.Name):
                    out[(node.name, item.target.id)] = values
    return out


def _literal_values(annotation) -> tuple:
    """The constants inside ``Literal[...]``, or () for anything else."""
    for node in ast.walk(annotation):
        if not isinstance(node, ast.Subscript):
            continue
        name = getattr(node.value, "id", getattr(node.value, "attr", ""))
        if name != "Literal":
            continue
        sl = node.slice
        items = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        values = tuple(i.value for i in items if isinstance(i, ast.Constant))
        if values:
            return values
    return ()


def scan(runs_dir: Path = RUNS) -> dict:
    """Accumulate emitted values for every Literal field, across all runs."""
    watched = literal_fields()
    observations = {
        key: FieldObservation(schema=key[0], field=key[1], allowed=allowed)
        for key, allowed in watched.items()
    }
    by_schema: dict = {}
    for (schema, name) in watched:
        by_schema.setdefault(schema, []).append(name)

    if not runs_dir.is_dir():
        return observations
    for path in sorted(runs_dir.glob("*/llm_calls.jsonl")):
        run_id = path.parent.name
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not row.get("ok"):
                continue
            schema = row.get("schema") or ""
            fields = by_schema.get(schema)
            if not fields:
                continue
            try:
                payload = json.loads(row.get("response_text") or "")
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            # The fields this call ACTUALLY asked for. Absent on records
            # written before the client logged it - those calls are counted
            # as seen but never as omissions, because a field cannot be
            # omitted from a schema that did not contain it yet.
            requested = row.get("schema_fields")
            for name in fields:
                obs = observations[(schema, name)]
                obs.calls += 1
                if requested is None:
                    known_requested = None
                else:
                    known_requested = name in requested
                if known_requested:
                    obs.asked += 1
                value = payload.get(name)
                if isinstance(value, str):
                    obs.runs.add(run_id)
                    if value in obs.allowed:
                        obs.counts[value] += 1
                    else:
                        # Outside the Literal. This is the RAW response,
                        # before pydantic - so it is evidence about whether
                        # the server constrained decoding, not about a value
                        # that shipped. The repair retry may well have fixed
                        # it; that it could be produced at all is the point.
                        obs.invalid[value] += 1
                elif known_requested:
                    obs.omitted += 1
    return observations


def main(argv=None) -> int:
    observations = scan()
    rows = sorted(observations.values(), key=lambda o: (-o.n, o.schema, o.field))
    print(f"  Literal fields watched: {len(rows)}  "
          f"(derived from the schemas, not declared)")
    print(f"  degeneracy floor: n={MIN_N}\n")
    for obs in rows:
        seen = ", ".join(f"{k} x{v}" for k, v in sorted(obs.counts.items()))
        print(f"  {obs.schema}.{obs.field}")
        print(f"    allowed  : {', '.join(obs.allowed)}")
        print(f"    emitted  : {seen or '(none)'}")
        print(f"    n={obs.n} across {len(obs.runs)} run(s); "
              f"asked={obs.asked}, omitted={obs.omitted}, "
              f"schema seen in {obs.calls} call(s)")
        if obs.invalid:
            print(f"    OUTSIDE THE LITERAL: "
                  + ", ".join(f"{k} x{v}"
                              for k, v in sorted(obs.invalid.items()))
                  + " - raw responses, pre-validation; evidence that guided "
                    "decoding did not constrain this field on those calls")
        print(f"    -> {obs.flag}")
        print()
    flagged = [o for o in rows
               if (o.single_valued and o.n >= MIN_N)
               or (o.asked >= MIN_N and o.omitted == o.asked)]
    print(f"  {len(flagged)} field(s) flagged as candidates for study.")
    print("  A flag is not a defect and this scan cannot find a wrong value -")
    print("  only a value never used. Accuracy still needs labels.")
    RECORD.write_text(json.dumps({
        "min_n": MIN_N,
        "what_this_finds": "fields that have only ever emitted one of their "
                           "allowed values, from run records alone",
        "what_this_cannot_find": "whether any emitted value is CORRECT. "
                                 "There are no labels in a run record. A "
                                 "clean distribution is not a correctness "
                                 "result.",
        "fields": [o.as_dict() for o in rows],
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {RECORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
