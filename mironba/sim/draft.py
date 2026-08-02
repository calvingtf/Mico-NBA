"""Draft simulation v0: rumor-driven assignment. One draft, no prospect data.

    python -m mironba.sim.draft --draft 2026

Three deliberate absences define v0 and are stated rather than papered over:

* **No prospect data.** The only inputs are ``draft_interest`` rows - dated,
  sourced reports of team behaviour (workouts, visits, reported intent).
  Published mocks are NOT inputs: they are a competing forecaster that lives
  on the baseline side in ``eval/draft_score.py``, and a fence test fails if
  this module so much as names their store file.
* **No lottery model.** Original slot order is reconstructed from standings
  (worst record first, both rounds); applied pick trades come from the
  transaction log where a single unambiguous conveyance can be read. Every
  slot that cannot be attributed is reported, not guessed.
* **No rookie-scale cap effects.** Assignment only. NOT_MODELLED.

The assignment walk is deterministic: slots in order, the owning team takes
its highest-priority available rumored target. Priority is DECLARED: a team's
targets are ordered by report date (an earlier link is a longer-standing
target), ties by row order in the store. Contested prospects resolve by pick
order, which is exact - the earlier slot picks first, no arbitrary branch.
A team with no remaining targets emits UNRESOLVED with the reason. The sim
never invents a pick.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence"
SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"

UNRESOLVED = "UNRESOLVED"


# --------------------------------------------------------------------------
# Pick ownership
# --------------------------------------------------------------------------


def _team_name_to_code(season: str) -> dict[str, str]:
    path = SNAPSHOTS / f"bbref-{season}" / "teams.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return {f"{r['city']} {r['name']}": r["team_id"]
                for r in csv.DictReader(handle)}


def standings_order(season: str) -> list[str]:
    """Worst record first, from the team game log - the pre-lottery order."""
    path = SNAPSHOTS / "nba-stats" / "game_logs.csv"
    wins: dict[str, int] = {}
    games: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["season"] != season:
                continue
            team = row["TEAM_ABBREVIATION"]
            games[team] = games.get(team, 0) + 1
            wins[team] = wins.get(team, 0) + (row["WL"] == "W")
    return sorted(games, key=lambda t: (wins[t] / games[t], t))


@dataclass
class Slot:
    number: int
    round: int
    original_owner: str
    owner: str
    via: str = "own"          # own | trade | UNATTRIBUTABLE
    note: str = ""


def _pick_trades(draft_year: int) -> list[dict]:
    """Unambiguous single-pick conveyances read from the transaction text.

    Only a trade whose text mentions exactly ONE '<year> Nth round draft
    pick', with exactly one 'is/was XXX own' tail and one receiving team, is
    attributed. Everything else - swaps, conditionals, multi-pick packages -
    is left for the UNATTRIBUTABLE report. Conservative by design: a wrong
    owner is worse than a reported gap.
    """
    conveyances = []
    mention = re.compile(rf"{draft_year} (1st|2nd) round draft pick")
    tail = re.compile(rf"{draft_year} (?:1st|2nd)-rd pick (?:is|was) ([A-Z]{{3}}) own\b")
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "transactions.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                text = row["text"]
                mentions = mention.findall(text)
                tails = tail.findall(text)
                if len(mentions) != 1 or len(tails) != 1:
                    continue
                receivers = re.findall(
                    rf"traded [^;]*{draft_year} {mentions[0]} round draft pick"
                    rf"[^;]* to the ([A-Z][a-zA-Z ]+?)(?: for | \.|\.|;|$)", text)
                if len(receivers) != 1:
                    continue
                conveyances.append({
                    "round": 1 if mentions[0] == "1st" else 2,
                    "original": tails[0],
                    "receiver_name": receivers[0].strip(),
                    "date": row["date"],
                })
    return conveyances


def build_slots(draft_year: int) -> list[Slot]:
    season = f"{draft_year - 1}-{str(draft_year)[-2:]}"
    order = standings_order(season)
    if len(order) != 30:
        raise RuntimeError(f"{season}: {len(order)} teams in standings")
    slots = [Slot(number=r * 30 + i + 1, round=r + 1, original_owner=t, owner=t)
             for r in range(2) for i, t in enumerate(order)]

    names = _team_name_to_code(season)
    contested: dict[tuple[str, int], int] = {}
    for conveyance in _pick_trades(draft_year):
        contested[(conveyance["original"], conveyance["round"])] = (
            contested.get((conveyance["original"], conveyance["round"]), 0) + 1)
    for conveyance in _pick_trades(draft_year):
        key = (conveyance["original"], conveyance["round"])
        slot = next((s for s in slots
                     if s.original_owner == conveyance["original"]
                     and s.round == conveyance["round"]), None)
        if slot is None:
            continue
        receiver = names.get(conveyance["receiver_name"])
        if contested[key] > 1:
            slot.via = "UNATTRIBUTABLE"
            slot.owner = ""
            slot.note = (f"{contested[key]} separate conveyances mention "
                         f"{key[0]}'s round-{key[1]} pick; chain not resolvable "
                         "from single-trade text")
        elif receiver is None:
            slot.via = "UNATTRIBUTABLE"
            slot.owner = ""
            slot.note = f"receiver {conveyance['receiver_name']!r} not mappable"
        else:
            slot.via = "trade"
            slot.owner = receiver
            slot.note = f"from {key[0]} per transaction of {conveyance['date']}"
    return slots


# --------------------------------------------------------------------------
# The input: draft_interest only
# --------------------------------------------------------------------------


def load_interest(draft_year: int) -> list[dict]:
    """The INPUT rows. This loader knows nothing about mocks; the competing
    forecaster lives in eval/ and a fence test keeps it there."""
    path = EVIDENCE / f"draft-{draft_year}" / "interest.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def targets_by_team(interest: list[dict]) -> dict[str, list[str]]:
    """Per team, targets in DECLARED priority: report date, then row order."""
    ordered: dict[str, list[str]] = {}
    for row in sorted(interest, key=lambda r: (r["team"], r["date"], r["id"])):
        bucket = ordered.setdefault(row["team"], [])
        if row["player"] not in bucket:
            bucket.append(row["player"])
    return ordered


# --------------------------------------------------------------------------
# Assignment
# --------------------------------------------------------------------------


@dataclass
class Assignment:
    slot: Slot
    player: str = ""
    status: str = UNRESOLVED
    reason: str = ""
    first_choice_gone: bool = False


@dataclass
class DraftResult:
    assignments: list[Assignment]
    cascade: int = 0

    @property
    def resolved(self) -> list[Assignment]:
        return [a for a in self.assignments if a.status == "RESOLVED"]


def run_draft(slots: list[Slot], targets: dict[str, list[str]]) -> DraftResult:
    taken: set[str] = set()
    out = []
    cascade = 0
    for slot in sorted(slots, key=lambda s: s.number):
        assignment = Assignment(slot=slot)
        if slot.via == "UNATTRIBUTABLE":
            assignment.reason = f"owner unattributable: {slot.note}"
            out.append(assignment)
            continue
        wanted = targets.get(slot.owner, [])
        if not wanted:
            assignment.reason = f"no rumored targets for {slot.owner}"
            out.append(assignment)
            continue
        available = [p for p in wanted if p not in taken]
        if wanted and available and wanted[0] != available[0]:
            assignment.first_choice_gone = True
            cascade += 1
        if wanted and not available and wanted[0] in taken:
            assignment.first_choice_gone = True
            cascade += 1
        if not available:
            assignment.reason = (f"{slot.owner}'s rumored targets all taken "
                                 f"({len(wanted)} listed)")
            out.append(assignment)
            continue
        assignment.player = available[0]
        assignment.status = "RESOLVED"
        taken.add(available[0])
        out.append(assignment)
    return DraftResult(assignments=out, cascade=cascade)


def main(argv=None) -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draft", type=int, default=2026)
    args = parser.parse_args(argv)

    slots = build_slots(args.draft)
    interest = load_interest(args.draft)
    targets = targets_by_team(interest)
    result = run_draft(slots, targets)

    own = sum(1 for s in slots if s.via == "own")
    traded = sum(1 for s in slots if s.via == "trade")
    unattr = [s for s in slots if s.via == "UNATTRIBUTABLE"]
    print(f"PICK OWNERSHIP ({args.draft} draft, order = standings worst-first; "
          "NO LOTTERY MODEL)")
    print(f"  attributed {own + traded}/60  ({own} own, {traded} via "
          f"unambiguous single-pick trade text)")
    for s in unattr:
        print(f"  UNATTRIBUTABLE slot {s.number:>2} ({s.original_owner} "
              f"r{s.round}): {s.note}")

    print(f"\nCORPUS: {len(interest)} dated interest rows, "
          f"{len(targets)} teams linked, "
          f"{len({r['player'] for r in interest})} prospects named")
    for team, wanted in sorted(targets.items()):
        print(f"  {team}: {len(wanted)} target(s): {', '.join(wanted[:4])}"
              + (" ..." if len(wanted) > 4 else ""))

    resolved = result.resolved
    print(f"\nASSIGNMENT: {len(resolved)} resolved, "
          f"{60 - len(resolved)} UNRESOLVED, "
          f"{result.cascade} slot(s) where the first choice was already gone")
    for a in result.assignments:
        if a.status == "RESOLVED":
            gone = "  [first choice was gone]" if a.first_choice_gone else ""
            print(f"  {a.slot.number:>2} {a.slot.owner} -> {a.player}{gone}")
    print("\n  unresolved reasons (never invented):")
    reasons: dict[str, int] = {}
    for a in result.assignments:
        if a.status == UNRESOLVED:
            key = a.reason.split("(")[0].strip()
            reasons[key] = reasons.get(key, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {count:>2}  {reason}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
