"""Find real trades that could become REALITY fixtures.

Joins the ingested transaction log to the ingested salary tables and scores
each trade against the coverage-matrix rows in README.md.

This **proposes**; it never promotes. Nothing here writes to
``tests/fixtures/real_trades.yaml`` and nothing sets ``verified: true``. A
candidate is a lead to check by hand, because the two things that decide a
fixture's verdict — each team's true apron salary on the trade date, and
whether any player was under base-year compensation — are exactly the two
things this data cannot establish.

Run: ``python -m mironba.data.candidates --snapshot bbref-2024-25``
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from mironba.rules.cap import ApronTier, exception_match_limit, pct_of, tier_for_salary
from mironba.rules.constants import TRADE_CUSHION, CapEnvironment, environment_for

SNAPSHOT_ROOT = Path(__file__).resolve().parent / "snapshots"

#: A team this close to an apron or cap line is excluded from tier-dependent
#: rows. Our team salary is a *sum of season cap hits*, which ignores dead
#: money and cap holds and cannot be dated to the trade; near a boundary that
#: error decides the tier, and therefore the verdict.
TIER_BOUNDARY_EXCLUSION = 3_000_000

TEAM_NAMES = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "LA Clippers": "LAC",
    "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}
_TEAM_ALT = "|".join(sorted(map(re.escape, TEAM_NAMES), key=len, reverse=True))

#: Each trade clause starts at "<Team> traded". Finding every such anchor and
#: slicing between them handles the three shapes Basketball-Reference uses: a
#: plain two-team sentence, a semicolon-separated multi-team trade, and
#: "In a 3-team trade, the X traded ... ; the Y traded ...".
_CLAUSE_START = re.compile(r"(?P<team>" + _TEAM_ALT + r")\s+traded\s+")
_CLAUSE_BODY = re.compile(
    r"(?P<out>.*?)\s+to the\s+(?P<b>" + _TEAM_ALT + r")(?:\s+for\s+(?P<back>.*))?",
    re.S,
)
_MARKER = re.compile(r"\{\{(\w+)\}\}")

#: A clause slice ends where the next one begins, which leaves the connective
#: that introduced it ("... ; the", "... and the") dangling on the tail.
_TRAILING_CONNECTIVE = re.compile(r"[\s;.,]+(?:and\s+)?the\s*$")


@dataclass
class Leg:
    player_id: str
    name: str
    salary: int | None
    from_team: str
    to_team: str


@dataclass
class Candidate:
    season: str
    date: date
    text: str
    legs: list[Leg] = field(default_factory=list)
    rows: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    score: int = 0

    @property
    def teams(self) -> set[str]:
        return {leg.from_team for leg in self.legs} | {leg.to_team for leg in self.legs}

    def outgoing(self, team: str) -> list[Leg]:
        return [leg for leg in self.legs if leg.from_team == team]

    def outgoing_total(self, team: str) -> int:
        return sum(leg.salary or 0 for leg in self.outgoing(team))

    @property
    def fully_priced(self) -> bool:
        return bool(self.legs) and all(leg.salary is not None for leg in self.legs)


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_snapshot(snapshot_id: str) -> tuple[dict, dict, list[dict], str]:
    """Returns (salary_by_player, name_by_player, transactions, season)."""
    directory = SNAPSHOT_ROOT / snapshot_id
    if not directory.exists():
        raise FileNotFoundError(f"no snapshot at {directory}")

    contracts = _read(directory / "contracts.csv")
    players = _read(directory / "players.csv")
    transactions = _read(directory / "transactions.csv")

    salary = {r["player_id"]: int(r["salary"]) for r in contracts}
    names = {r["player_id"]: r["name"] for r in players}
    season = contracts[0]["season"]
    return salary, names, transactions, season


def team_salary_estimate(snapshot_id: str) -> dict[str, int]:
    """Sum of season cap hits per team.

    An approximation of apron salary, not a substitute for one: it omits dead
    money and cap holds, and it is a season figure rather than a figure as of
    a trade date. Callers must respect ``TIER_BOUNDARY_EXCLUSION``.
    """
    contracts = _read(SNAPSHOT_ROOT / snapshot_id / "contracts.csv")
    totals: dict[str, int] = defaultdict(int)
    for row in contracts:
        totals[row["team_id"]] += int(row["salary"])
    return dict(totals)


def parse_trade(marked_text: str, names: dict[str, str], salary: dict[str, int]) -> list[Leg]:
    """Split a marked-up transaction into per-player legs.

    Expects ``marked_text`` — the prose with ``{{bbref_id}}`` after each player
    name. Draft picks and cash appear as ordinary words with no marker and are
    simply not emitted; a clause containing only picks yields no legs, which is
    correct rather than a parse failure.
    """
    anchors = list(_CLAUSE_START.finditer(marked_text))
    legs: list[Leg] = []

    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(marked_text)
        clause = _TRAILING_CONNECTIVE.sub("", marked_text[anchor.end() : end].rstrip(" .;,"))
        body = _CLAUSE_BODY.match(clause)
        if not body:
            continue

        source = TEAM_NAMES[anchor.group("team")]
        destination = TEAM_NAMES[body.group("b")]
        for segment, src, dst in (
            (body.group("out") or "", source, destination),
            (body.group("back") or "", destination, source),
        ):
            for pid in dict.fromkeys(_MARKER.findall(segment)):
                legs.append(Leg(pid, names.get(pid, pid), salary.get(pid), src, dst))
    return legs


def _tier_confidence(team: str, salaries: dict[str, int], env: CapEnvironment):
    """Tier plus distance to the nearest boundary, or None if too close."""
    total = salaries.get(team)
    if total is None:
        return None, None
    lines = (env.salary_cap, env.first_apron, env.second_apron)
    margin = min(abs(total - line) for line in lines)
    if margin < TIER_BOUNDARY_EXCLUSION:
        return None, margin
    return tier_for_salary(total, env), margin


def classify(candidate: Candidate, salaries: dict[str, int], env: CapEnvironment) -> None:
    """Tag a candidate with the matrix rows it could serve as evidence for."""
    text = candidate.text.lower()

    if len(candidate.teams) < 2 or not candidate.legs:
        candidate.notes.append("no player legs parsed")
        return
    if not candidate.fully_priced:
        missing = [leg.name for leg in candidate.legs if leg.salary is None]
        candidate.notes.append(f"unpriced: {', '.join(missing[:3])}")

    # Bracket rows key on one team's total outgoing salary.
    lower_edge = env.expanded_tpe - TRADE_CUSHION
    upper_edge = (env.expanded_tpe - TRADE_CUSHION) * 4

    for team in candidate.teams:
        out = candidate.outgoing_total(team)
        if out <= 0:
            continue
        if 7_250_000 <= out <= 7_500_000:
            candidate.rows.append("contested-band")
            candidate.score += 100  # rarest row; weight it heavily
        if out < lower_edge:
            candidate.rows.append("small-bracket")
        elif out <= upper_edge:
            candidate.rows.append("middle-bracket")
        else:
            candidate.rows.append("large-bracket")

        if len(candidate.outgoing(team)) >= 2:
            candidate.rows.append("aggregation-below-apron")
            candidate.score += 10

        tier, margin = _tier_confidence(team, salaries, env)
        if tier is None:
            candidate.notes.append(
                f"{team} excluded from tier rows"
                + (f" (within ${margin:,} of a boundary)" if margin is not None else "")
            )
            continue
        if tier is ApronTier.FIRST_APRON:
            candidate.rows.append("first-apron-matching")
            candidate.score += 20
        elif tier is ApronTier.SECOND_APRON:
            candidate.rows.append("second-apron-matching")
            candidate.score += 20
            if len(candidate.outgoing(team)) >= 2:
                candidate.rows.append("second-apron-aggregation")
                candidate.score += 30

    # A team receiving salary while sending none is absorbing into room or a
    # trade exception; the transaction text does not say which.
    for team in candidate.teams:
        incoming = sum(
            leg.salary or 0 for leg in candidate.legs if leg.to_team == team
        )
        if incoming > 0 and candidate.outgoing_total(team) == 0:
            candidate.rows.append("tpe-absorption")
            candidate.notes.append(
                f"{team} takes salary for none — confirm room vs trade exception"
            )
            candidate.score += 15

    if "sign-and-trade" in text or "signed and traded" in text:
        candidate.rows.append("sign-and-trade")
        candidate.score += 25
    if "cash" in text:
        candidate.notes.append("cash mentioned — check second-apron cash ban")
        candidate.score += 5

    candidate.rows = sorted(set(candidate.rows))
    candidate.score += 2 * len(candidate.legs)


def build_candidates(snapshot_id: str) -> tuple[list[Candidate], str]:
    salary, names, transactions, season = load_snapshot(snapshot_id)
    env = environment_for(season)
    salaries = team_salary_estimate(snapshot_id)

    out: list[Candidate] = []
    for row in transactions:
        if row["is_trade"] != "1":
            continue
        candidate = Candidate(
            season=season,
            date=date.fromisoformat(row["date"]),
            text=row["text"],
        )
        candidate.legs = parse_trade(row["marked_text"], names, salary)
        classify(candidate, salaries, env)
        if candidate.rows:
            out.append(candidate)

    out.sort(key=lambda c: -c.score)
    return out, season


ROWS_NEEDING_REALITY = [
    "second-apron-aggregation",
    "second-apron-cash",
    "aggregation-below-apron",
    "tpe-absorption",
    "sign-and-trade",
    "base-year-compensation",
]


def report(snapshot_ids: list[str], limit: int = 6) -> str:
    lines: list[str] = ["# Fixture candidates", ""]
    per_row: dict[str, int] = defaultdict(int)
    total = 0

    for snapshot_id in snapshot_ids:
        try:
            candidates, season = build_candidates(snapshot_id)
        except FileNotFoundError as exc:
            lines.append(f"## {snapshot_id}\n\nNOT INGESTED: {exc}\n")
            continue

        total += len(candidates)
        lines.append(f"## {snapshot_id} ({season}) — {len(candidates)} candidates")
        lines.append("")
        by_row: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in candidates:
            for row in candidate.rows:
                by_row[row].append(candidate)
                per_row[row] += 1

        for row in sorted(by_row):
            lines.append(f"### {row} — {len(by_row[row])}")
            for candidate in by_row[row][:limit]:
                legs = ", ".join(
                    f"{leg.name} ${leg.salary:,}" if leg.salary else f"{leg.name} (?)"
                    for leg in candidate.legs[:4]
                )
                lines.append(f"- **{candidate.date}** score {candidate.score} — {legs}")
                for note in candidate.notes[:2]:
                    lines.append(f"  - note: {note}")
            lines.append("")

    lines.append("## Totals by matrix row")
    lines.append("")
    lines.append("| Row | Candidates |")
    lines.append("| --- | --- |")
    for row in sorted(per_row):
        lines.append(f"| {row} | {per_row[row]} |")
    for row in ROWS_NEEDING_REALITY:
        if row not in per_row:
            lines.append(f"| {row} | 0 — NO VIABLE CANDIDATE |")
    lines.append("")
    lines.append(
        "Every candidate still needs, by hand: each team's true apron salary on "
        "the trade date, and base-year-compensation status for each outgoing "
        "player. Neither is in the ingested data."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank fixture candidates.")
    parser.add_argument("--snapshots", nargs="+", default=["bbref-2023-24", "bbref-2024-25"])
    parser.add_argument("--out", default="candidate_report.md")
    args = parser.parse_args(argv)

    text = report(args.snapshots)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
