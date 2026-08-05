"""How many fields each model call asks for, enumerated.

    python -m mironba.llm.schema_audit

Entry #74 measured a field that was **inert inside a large schema and 12/12
alone**: `event` in the eight-field `Proposal` scored exactly the null (6/12)
and never once emitted the minority class, while the same question asked on
its own scored 12/12 and ran 3.6x faster. That is not a fact about that
field. It is a fact about asking a small model for many things at once, and
it makes "how many fields does this call ask for" a number worth knowing for
every call rather than for the one that happened to be caught.

**This is a registry, not a lint.** A large schema is not automatically
wrong: `TradeIntent` is large because a trade intent genuinely has that many
parts, and splitting it would cost round trips that buy nothing if the
fields are already answered. What the registry forbids is a call whose field
count nobody has looked at. Each entry declares a disposition:

``MEASURED``
    Split, or kept, on the strength of a measurement with its own null.

``CANDIDATE``
    Multi-field and plausibly splittable, not yet measured. Named so, with
    the round-trip cost stated, so the queue is visible instead of implied.

``SINGLE``
    One field. Nothing to split.

``BY-DESIGN``
    Multi-field and deliberately kept whole, with the reason.

The rule the charter takes from #74 is *measure one candidate before
generalising*. Splitting every multi-field call on principle would be the
same mistake in the other direction - a change made on a rule of thumb
rather than on evidence, which is what the null discipline exists to stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: The ``mironba`` package directory; module paths in the registry are
#: relative to it.
PACKAGE = Path(__file__).resolve().parents[1]

class SchemaNotFound(LookupError):
    """A registry entry names a schema the package does not define."""


MEASURED = "measured"
CANDIDATE = "candidate"
SINGLE = "single-field"
BY_DESIGN = "by-design"


@dataclass(frozen=True)
class CallAudit:
    """A call site's DISPOSITION. The field count is never declared here.

    The first draft of this registry declared the counts by hand and got two
    of ten wrong - TradeIntent as four fields when it has five,
    BranchSummary as four when it has two. A registry whose numbers are
    typed in drifts from the code it claims to describe, which is the exact
    failure it exists to prevent. Counts come from ``field_counts()``, which
    reads the schema definitions out of the source.
    """

    purpose: str
    module: str
    schema: str
    disposition: str
    note: str


#: Every call site that passes a schema. Exhaustive over the codebase by
#: test - a new ``purpose=`` that is not listed here fails.
CALL_AUDIT: tuple[CallAudit, ...] = (
    # -- the authoring path -------------------------------------------------
    CallAudit(
        purpose="scenario_draft", module="world/authoring.py",
        schema="Proposal", disposition=CANDIDATE,
        note="The schema #74 was measured inside. `event` was split out of "
             "it and went 6/12 -> 12/12. Several fields remain, of which "
             "`kind` is the next candidate: it is the other CLASSIFIER in "
             "the schema, so it can be measured the same way against the "
             "same kind of null. `moves` was already split for the same "
             "symptom (empty on 3 of 4 sentences). Round-trip cost of "
             "splitting one more field: +1 call, ~49s measured, against a "
             "p50 of 3.2 min - about 25% more wall clock.",
    ),
    CallAudit(
        purpose="event_classification", module="world/authoring.py",
        schema="EventKind", disposition=MEASURED,
        note="Entry #74. The split itself: 6/12 inert in the big schema, "
             "12/12 alone, 176s -> 49s. Null: always-'trade' scores 6/12 by "
             "construction on a balanced set.",
    ),
    CallAudit(
        purpose="scenario_draft_moves", module="world/authoring.py",
        schema="Moves", disposition=MEASURED,
        note="Split before #74 and for the same symptom, though it was not "
             "framed as a null then: the one-shot returned empty moves on "
             "three of the first four sentences. Kept whole internally "
             "because a movement's three fields are one answer - a "
             "player_name with no to_team is not half an answer, it is none.",
    ),
    # -- the agent loop -----------------------------------------------------
    CallAudit(
        purpose="action_choice", module="agents/gm.py",
        schema="ActionChoice", disposition=BY_DESIGN,
        note="This call IS the charter's two-step: pick an action type from "
             "an enum first, fill that action's parameters in a second "
             "call. Splitting the reason off the choice would leave a "
             "choice nobody can audit.",
    ),
    CallAudit(
        purpose="trade_intent", module="agents/gm.py",
        schema="TradeIntent", disposition=CANDIDATE,
        note="No salaries by construction. Whether any field is inert is "
             "UNMEASURED. It needs a different null from a classifier's - "
             "these are extractions and id lists, not labels - so the "
             "measurement is not a copy of #74 and is not claimed as done.",
    ),
    CallAudit(
        purpose="trade_intent_retry", module="agents/gm.py",
        schema="TradeIntent", disposition=CANDIDATE,
        note="The repair retry for the above; same schema, same open "
             "question.",
    ),
    CallAudit(
        purpose="package_selection", module="agents/gm.py",
        schema="PackageSelection", disposition=BY_DESIGN,
        note="An index and a reason. The index is the answer and the reason "
             "is what makes it reviewable; one without the other is not a "
             "smaller call, it is a worse one.",
    ),
    CallAudit(
        purpose="report", module="agents/report.py",
        schema="BranchSummary", disposition=BY_DESIGN,
        note="Prose only. Nothing downstream computes on it and no field "
             "is a label, so there is no degenerate class to detect and "
             "nothing a split would protect.",
    ),
    CallAudit(
        purpose="agent_chat", module="agents/chat.py",
        schema="AgentReply", disposition=BY_DESIGN,
        note="Conversational surface; not on any measured path and not "
             "read by any scored code.",
    ),
    CallAudit(
        purpose="curation_draft", module="data/ingest/rss.py",
        schema="Draft", disposition=CANDIDATE,
        note="Named `Draft`, which collides with the authoring dataclass "
             "of the same name - the audit distinguishes them by base "
             "class, not by name. On the archive curation path, "
             "human-gated before "
             "anything is written, so an inert field surfaces as a bad "
             "draft a person rejects rather than as a silent wrong number - "
             "which lowers the priority, not the question.",
    ),
)



def _schema_fields(source: str) -> dict[str, list[str]]:
    """class name -> annotated field names, from one module's source.

    AST rather than import, because three of the authoring schemas are
    defined INSIDE the functions that use them and cannot be imported at
    all. ``ast.walk`` finds those the same as any other.
    """
    import ast

    out: dict[str, list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
        if "BaseModel" not in bases:
            continue
        out[node.name] = [
            item.target.id for item in node.body
            if isinstance(item, ast.AnnAssign)
            and isinstance(item.target, ast.Name)
        ]
    return out


def _nested_names(source: str, schema: str) -> list[str]:
    """Schema classes referenced by ``schema``'s own annotations."""
    import ast

    defined = set(_schema_fields(source))
    referenced: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ClassDef) and node.name == schema):
            continue
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or item.annotation is None:
                continue
            for inner in ast.walk(item.annotation):
                name = getattr(inner, "id", "")
                if name in defined and name != schema:
                    referenced.append(name)
    return referenced


def field_counts(row: CallAudit) -> tuple[int, int]:
    """(top-level fields, nested fields) for one call site's schema.

    Nested fields count because the model emits them: a ``list[Move]`` of
    three fields is three more things to get right, whatever the container.

    The whole package is searched, not ``row.module``: a call site and the
    schema it passes are usually in different files - the GM agent's
    schemas live in ``llm/schemas.py`` - and looking only where the call is
    returned zero for four of ten entries on the first attempt. Zero is a
    plausible-looking number here, which is what makes it dangerous.
    """
    for path in sorted(PACKAGE.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"class {row.schema}(" not in source:
            continue
        per_class = _schema_fields(source)
        fields = per_class.get(row.schema)
        if fields is None:
            continue
        nested = sum(len(per_class.get(name, ()))
                     for name in _nested_names(source, row.schema))
        return (len(fields), nested)
    raise SchemaNotFound(
        f"{row.purpose}: no BaseModel named {row.schema!r} anywhere in the "
        "package. Returning zero here would be worse than failing: a "
        "schema that cannot be found and a schema with no fields both read "
        "as 0, and they call for opposite responses - the same confusion "
        "the degenerate-predictor rule is about. Caught when this registry "
        "named the RSS curation schema CurationDraft; it is called Draft."
    )


def by_purpose() -> dict[str, CallAudit]:
    return {row.purpose: row for row in CALL_AUDIT}


def candidates() -> list[CallAudit]:
    """Multi-field calls nobody has measured yet. The visible queue."""
    return [row for row in CALL_AUDIT if row.disposition == CANDIDATE]


def main(argv=None) -> int:
    rows = [(row, *field_counts(row)) for row in CALL_AUDIT]
    print(f"  {'purpose':<26} {'fields':>6} {'nested':>7} {'total':>6}  "
          "disposition")
    for row, fields, nested in sorted(rows, key=lambda r: -(r[1] + r[2])):
        print(f"  {row.purpose:<26} {fields:>6} {nested:>7} "
              f"{fields + nested:>6}  {row.disposition}")
    queue = [(r, f, n) for r, f, n in rows if r.disposition == CANDIDATE]
    print(f"\n  {len(CALL_AUDIT)} call sites; {len(queue)} unmeasured "
          "multi-field candidate(s):")
    for row, fields, nested in queue:
        print(f"    {row.purpose} ({fields + nested} fields) - {row.module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
