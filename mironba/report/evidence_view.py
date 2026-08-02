"""The evidence layer, rendered: what was known when, and what forked the world.

Pure helpers for the product surface. No new curation, no new measurement -
every row rendered here already exists in the ledger, and the claim the display
makes is the only one the news layer has earned: **not that it predicts, but
that every input is dated, sourced, anchored, and on the correct side of the
freeze.**

Two hard rules, both inherited:

* Interest is an **input, not a prediction**. Suitor identification is retired
  as a scored metric (measurements entry 42), and the display says so in words
  wherever interest appears.
* The branch-matching rule for conditionals is **declared, not inferred** -
  entry 44 is what undeclared matching does. The rule, verbatim: a conditional
  whose condition names Golden State fires in the branch whose name contains
  "blocker"; every other conditional fires in the other branch. It is the same
  rule the conditionals_fire check scored 4/4 (p=0.0625, suggestive only).
"""

from __future__ import annotations

INPUT_MARKER = (
    "Reported interest is an INPUT, not a prediction - suitor identification "
    "is retired as a scored metric because once interest seeds the set, "
    "identifying it is stipulated."
)


def load_scenario_ledger(scenario_id: str):
    """The named scenario's ledger, or None when its files are absent."""
    try:
        from mironba.world.scenario import load_scenario

        return load_scenario(scenario_id).ledger()
    except Exception:  # noqa: BLE001 - surface renders fine without evidence
        return None


def known_at_freeze(ledger) -> list[dict]:
    """PRE-freeze interest rows, dated and sourced, oldest first.

    world_state-side only: POST rows are the answer and never reach a surface
    that renders inputs.
    """
    from mironba.report.timeline import name_of

    rows = []
    for row in sorted(ledger.reported_interest(), key=lambda r: (r.date, r.id)):
        rows.append({
            "id": row.id,
            "date": row.date.isoformat(),
            "team": row.team,
            "player": name_of(row.player_id),
            "source": row.source,
            "url": row.url,
            "anchors": row.anchors,
            "note": row.note,
        })
    return rows


def condition_fires_in(condition: str, branch_name: str, scenario=None) -> bool:
    """The declared rule - the SCENARIO's marker rule, never local inference."""
    if scenario is not None:
        for key in scenario.branches:
            if key in branch_name:
                return scenario.condition_fires_in(condition, key)
    gsw_conditional = "golden state" in condition.lower()
    gsw_branch = "blocker" in branch_name.lower()
    return gsw_conditional == gsw_branch


def branch_conditionals(ledger, branch_name: str) -> list[dict]:
    """Every open conditional, with whether it fires in this branch."""
    out = []
    for cond in ledger.open_conditionals():
        out.append({
            "id": cond.id,
            "subject": cond.subject,
            "condition": cond.condition,
            "commitment": cond.commitment,
            "date": cond.date.isoformat(),
            "source": cond.reported_by,
            "url": cond.url,
            "fired": condition_fires_in(cond.condition, branch_name),
        })
    return out


def render_known_text(ledger, width: int = 78) -> str:
    """Terminal block: what was known at the freeze, dated and sourced."""
    rows = known_at_freeze(ledger)
    if not rows:
        return ""
    lines = ["-" * width,
             "  KNOWN AT THE FREEZE - dated, sourced inputs (not predictions)",
             "-" * width]
    for row in rows:
        lines.append(f"  {row['date']}  {row['team']} in on {row['player']}"
                     f"   [{row['id']} <- {row['anchors']}]")
        lines.append(f"            {row['source']}  {row['url'][:60]}")
    lines.append(f"  {INPUT_MARKER}")
    lines.append("-" * width)
    return chr(10).join(lines)
