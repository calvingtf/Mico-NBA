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
    Both outcomes have now happened inside one schema: `event` was split
    (#74), `kind` was measured and KEPT (#77). "Measured" does not mean
    "split".

``CANDIDATE``
    Multi-field and plausibly splittable, not yet measured. Named so, with
    two things stated: whether a split is VIABLE at all, and what it COSTS
    in round trips. Cost is not one number - a fixed +49s on a
    once-per-draft call is not the same as +1 round trip on a call that
    runs once per waking team per tick, or once per archived article. The
    queue is visible instead of implied.

``SINGLE``
    One field. Nothing to split.

``BY-DESIGN``
    Multi-field and deliberately kept whole, with the reason.

The rule the charter takes from #74 is *measure one candidate before
generalising*. Splitting every multi-field call on principle would be the
same mistake in the other direction - a change made on a rule of thumb
rather than on evidence, which is what the null discipline exists to stop.

**The queue below is not worked through by A/B alone.** #77 showed schema
size predicts nothing - `event` was inert and `kind`, beside it in the same
schema, was flawless - so deciding each field soundly would mean a
12-sentence labelled study per field. ``llm/degeneracy.py`` watches the
cheap half instead: it reads what every Literal field has actually emitted
across recorded runs and flags the ones that are constants, with no labels
and no study. It cannot say a field is WRONG, only that it never varies or
never answers. A candidate here is promoted to a study when that monitor
flags it, or when someone has a reason - not on a rota.
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
        note="Two of its fields are now measured and they disagree. "
             "`event` was inert inside this schema (6/12, never emitted the "
             "minority class) and 12/12 alone, so it was split - #74. "
             "`kind`, the OTHER classifier in the same schema, scores 12/12 "
             "INSIDE it, both classes emitted, identical to the dedicated "
             "call - #77 - so it stays, and splitting it would cost a round "
             "trip for nothing. A large schema is therefore not the "
             "diagnosis; it is the place to look. The remaining fields are "
             "extractions rather than labels and are UNMEASURED. COST of "
             "splitting one more: +1 round trip per draft, ~49s measured, "
             "against a p50 of 3.2 min - about 25% more wall clock, paid "
             "once per authored scenario rather than per tick.",
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
        note="No salaries by construction. VIABLE to split into a target "
             "call and a tradeable-assets call; the reason field would go "
             "with whichever it explains. Whether any field is inert is "
             "UNMEASURED, and it needs a different null from a "
             "classifier's - these are id lists, not labels, so the null is "
             "'the empty list' or 'every eligible id', not a majority "
             "class. COST: +1 round trip per intent, and the intent loop "
             "runs once per waking team per tick - the only call here on a "
             "per-tick path, so a split multiplies across the run rather "
             "than costing a fixed 49s once.",
    ),
    CallAudit(
        purpose="trade_intent_retry", module="agents/gm.py",
        schema="TradeIntent", disposition=CANDIDATE,
        note="The repair retry for the above; same schema, same open "
             "question. VIABLE only in lockstep with `trade_intent` - "
             "splitting one without the other leaves the retry unable to "
             "repair what the first call produced. COST: the same +1 round "
             "trip per intent, on the same per-tick path, and only on "
             "intents that already failed validation once.",
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
        note="VIABLE to split: `kind` is a classifier and could be "
             "measured exactly as #74 was, with the remaining fields asked "
             "only for the kind that needs them. COST: +1 round trip per "
             "ARTICLE, and the archiver polls twice daily across many "
             "feeds - the highest-volume call site here, so this is the one "
             "where a split is most expensive and most likely to pay. "
             "Named `Draft`, which collides with the authoring dataclass "
             "of the same name; the audit distinguishes them by base "
             "class, not by name. Human-gated before "
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
