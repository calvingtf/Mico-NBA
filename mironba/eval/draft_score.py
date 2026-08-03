"""Score the draft assignment against exact ground truth and TWO nulls.

    python -m mironba.eval.draft_score --draft 2026

Ground truth is exact - who was actually picked at each slot, from a sourced
store file only this module and the tests read. Scoring is on RESOLVED slots
only, with the unresolved count printed beside every number so the
denominator is visible.

* **Null 1 - random assignment**: named prospects shuffled onto the resolved
  slots, Monte Carlo, seeded. Beating this is expected and means little.
* **Null 2 - the competing forecaster**: a published final mock's predictions
  on the SAME slots. This is the real test. Losing to it is a perfectly good
  result and is reported in those words when it happens: a mock is a
  professional forecast built on prospect data the sim deliberately does not
  have.
"""

from __future__ import annotations

import csv
import random
import unicodedata
from pathlib import Path

EVIDENCE = Path(__file__).resolve().parents[2] / "evidence"


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(c for c in text.lower() if c.isalpha())


def actual_picks(draft_year: int) -> dict[int, str]:
    """EVAL-ONLY ground truth: slot -> normalised player name."""
    path = EVIDENCE / f"draft-{draft_year}" / "actual-picks.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return {int(r["slot"]): _norm(r["player"]) for r in csv.DictReader(handle)}


def projections(draft_year: int) -> dict[int, str]:
    """The competing forecaster's slot claims. Baseline side, never an input."""
    path = EVIDENCE / f"draft-{draft_year}" / "projections.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {int(r["slot"]): _norm(r["player"]) for r in csv.DictReader(handle)}


def score(draft_year: int, *, trials: int = 20000, seed: int = 20260625) -> dict:
    from mironba.sim.draft import (
        build_slots, load_interest, run_draft, targets_by_team,
    )

    interest = load_interest(draft_year)
    result = run_draft(build_slots(draft_year), targets_by_team(interest))
    truth = actual_picks(draft_year)
    mock = projections(draft_year)

    resolved = [(a.slot.number, _norm(a.player)) for a in result.resolved]
    hits = sum(1 for slot, player in resolved if truth.get(slot) == player)

    # Null 1: the named-prospect pool shuffled onto the same slots.
    pool = sorted({_norm(r["player"]) for r in interest})
    rng = random.Random(seed)
    total = 0
    for _ in range(trials):
        draw = rng.sample(pool, k=min(len(resolved), len(pool)))
        total += sum(1 for (slot, _), player in zip(resolved, draw)
                     if truth.get(slot) == player)
    null1 = total / trials

    # Null 2: the mock on the SAME slots (only where it states a claim).
    covered = [(slot, player) for slot, player in resolved if slot in mock]
    mock_hits = sum(1 for slot, _ in covered if mock[slot] == truth.get(slot))
    sim_hits_covered = sum(1 for slot, player in covered
                           if truth.get(slot) == player)

    return {
        "resolved": len(resolved), "unresolved": 60 - len(resolved),
        "cascade": result.cascade,
        "hits": hits, "null1_expected": null1,
        "null2_slots": len(covered), "null2_mock_hits": mock_hits,
        "null2_sim_hits": sim_hits_covered,
        "pool_size": len(pool),
    }


#: Team-name -> code map for the actual-picks table (curated corpus teams).
_TEAM_CODES = {
    "Washington Wizards": "WAS", "Golden State Warriors": "GSW",
    "Oklahoma City Thunder": "OKC", "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL", "Charlotte Hornets": "CHA",
    "Dallas Mavericks": "DAL", "Los Angeles Clippers": "LAC",
    "Atlanta Hawks": "ATL", "Chicago Bulls": "CHI",
    "New Orleans Pelicans": "NOP",
}


def conditional_score(draft_year: int) -> dict:
    """Score the contingency, not the slot: first choice gone, then what?

    A mock gives a point prediction and cannot say what a team does when its
    target is taken; the cascade is the sim's one unique output. This walks
    the ACTUAL draft - not the reconstructed order, whose lottery-free slots
    are an artifact - and at each corpus team's real pick asks: was the
    team's rumored first choice already taken? If so, does the actual pick
    match the priority list's next available name?

    The null is NOT unconditional slot accuracy: once the first choice is
    gone the candidate set is the team's remaining un-taken targets, so
    chance is 1/len(remaining) - and a case whose actual pick lies outside
    that set is UNINFORMATIVE BY CONSTRUCTION (chance scores zero there too,
    the same shape as the retired external_acquisition_overlap ceiling).
    """
    import csv as _csv

    from mironba.sim.draft import load_interest, targets_by_team

    truth_rows = []
    path = EVIDENCE / f"draft-{draft_year}" / "actual-picks.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        truth_rows = sorted(_csv.DictReader(handle), key=lambda r: int(r["slot"]))

    targets = {team: [_norm(w) for w in wanted]
               for team, wanted in targets_by_team(load_interest(draft_year)).items()}

    taken: set[str] = set()
    conditional = exhausted = first_hits = first_available = 0
    cases = []
    for row in truth_rows:
        code = _TEAM_CODES.get(row["team"])
        actual = _norm(row["player"])
        if code in targets:
            wanted = targets[code]
            if wanted[0] not in taken:
                first_available += 1
                first_hits += actual == wanted[0]
            else:
                conditional += 1
                remaining = [w for w in wanted if w not in taken]
                if not remaining:
                    exhausted += 1
                else:
                    cases.append({
                        "slot": int(row["slot"]), "team": code,
                        "predicted": remaining[0], "actual": actual,
                        "hit": remaining[0] == actual,
                        "set_size": len(remaining),
                        "informative": actual in remaining,
                    })
        taken.add(actual)

    informative = [c for c in cases if c["informative"]]
    return {
        "conditional_events": conditional,
        "exhausted": exhausted,
        "cases": cases,
        "n": len(cases),
        "n_informative": len(informative),
        "hits": sum(c["hit"] for c in cases),
        "null_expected": sum(1 / c["set_size"] for c in informative),
        "first_choice_available": first_available,
        "first_choice_hits": first_hits,
    }


def print_conditional(draft_year: int) -> None:
    c = conditional_score(draft_year)
    print()
    print("CONDITIONAL - the cascade, scored on the ACTUAL draft walk")
    print(f"  n = {c['n']} scoreable case(s), stated before any rate: a rate "
          "on this few cases is a")
    print("  diagnostic, not a measurement (the standard that retired "
          "suitor_won at n=1).")
    print(f"  {c['conditional_events']} first-choice-gone event(s) in "
          f"reality; {c['exhausted']} had no remaining rumored target - "
          "single-target teams cannot cascade.")
    for case in c["cases"]:
        tag = "" if case["informative"] else             "  UNINFORMATIVE BY CONSTRUCTION (actual outside the remaining set; chance scores 0 too)"
        print(f"    slot {case['slot']:>2} {case['team']}: fallback "
              f"{case['predicted']!r} vs actual {case['actual']!r} "
              f"{'HIT' if case['hit'] else 'MISS'}   null 1/{case['set_size']}{tag}")
    print(f"  fallback hits {c['hits']}/{c['n']}   conditional null "
          f"{c['null_expected']:.2f} expected on the {c['n_informative']} "
          "informative case(s)")
    print(f"  (context: when the first choice was still available, teams took "
          f"it {c['first_choice_hits']}/{c['first_choice_available']} times)")
    print("  VERDICT: the corpus cannot support this measurement. It needs "
          "RANKED lists 3-4 deep")
    print("  per team (~120-150 rows shaped like GSW's ten), giving ~20-25 "
          "informative cases -")
    print("  enough to separate a 50% fallback rate from a 1/4 null. At n like "
          "this, stop.")


def main(argv=None) -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draft", type=int, default=2026)
    args = parser.parse_args(argv)

    s = score(args.draft)
    print(f"DRAFT {args.draft} - scored on resolved slots only")
    print(f"  resolved {s['resolved']} of 60   (unresolved {s['unresolved']}, "
          f"stated beside every number below)")
    print(f"  cascade: first choice already gone at {s['cascade']} slot(s)")
    print()
    print(f"  accuracy         {s['hits']}/{s['resolved']} exact "
          f"player-at-slot hits")
    print(f"  null 1 (random)  {s['null1_expected']:.2f} expected hits "
          f"({s['pool_size']} named prospects shuffled onto the same slots, "
          "seeded Monte Carlo)")
    print(f"  null 2 (mock)    {s['null2_mock_hits']}/{s['null2_slots']} on the "
          f"same slots the mock covers; the sim scores "
          f"{s['null2_sim_hits']}/{s['null2_slots']} there")
    print()
    beat1 = s["hits"] > s["null1_expected"]
    print(f"  vs null 1: {'above' if beat1 else 'NOT above'} chance - expected, "
          "and means little either way at this n")
    if s["null2_slots"] == 0:
        print("  vs null 2: the mock covers none of the resolved slots; "
              "no comparison possible")
    elif s["null2_sim_hits"] < s["null2_mock_hits"]:
        print("  vs null 2: THE SIM LOSES TO THE CONSENSUS MOCK - a perfectly "
              "good result, said plainly. The mock is a professional forecast "
              "built on prospect evaluation; the sim has only dated rumor "
              "behaviour, and this number is what that difference is worth.")
    elif s["null2_sim_hits"] == s["null2_mock_hits"]:
        print("  vs null 2: tied with the consensus mock on covered slots.")
    else:
        print("  vs null 2: above the consensus mock on covered slots - at "
              f"n={s['null2_slots']} treat as suggestive, not significant.")
    print_conditional(args.draft)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
