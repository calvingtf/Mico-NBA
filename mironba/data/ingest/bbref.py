"""Basketball-Reference parsing.

Two quirks drive everything here:

  * Secondary tables are wrapped in HTML comments to discourage scrapers, so
    the markup has to be unwrapped before any table is visible.
  * Team abbreviations differ from the ones the rest of this codebase uses
    (BRK/CHO/PHO vs BKN/CHA/PHX), so every code crossing the boundary is
    mapped in one place rather than at each call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

BASE = "https://www.basketball-reference.com"

#: Basketball-Reference code -> the code used everywhere else in this project.
TEAM_CODE = {
    "ATL": "ATL", "BOS": "BOS", "BRK": "BKN", "CHO": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
    "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHO": "PHX", "POR": "POR",
    "SAC": "SAC", "SAS": "SAS", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS",
}

BBREF_CODES = tuple(TEAM_CODE)

_COMMENT = re.compile(r"<!--|-->")
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def season_end_year(season: str) -> int:
    """``"2023-24"`` -> ``2024``, the year Basketball-Reference keys pages by."""
    return int(season[:4]) + 1


def unwrap(html: str) -> str:
    """Strip comment markers so comment-hidden tables become parseable."""
    return _COMMENT.sub("", html)


def text_of(fragment: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", fragment)).strip()


def team_season_url(bbref_code: str, season: str) -> str:
    return f"{BASE}/teams/{bbref_code}/{season_end_year(season)}.html"


def transactions_url(season: str) -> str:
    return f"{BASE}/leagues/NBA_{season_end_year(season)}_transactions.html"


# --------------------------------------------------------------------------
# Salaries
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SalaryRow:
    player_id: str
    name: str
    team_id: str
    season: str
    salary: int


_SALARY_TABLE = re.compile(r'<table[^>]*id="salaries2".*?</table>', re.S)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_PLAYER_ID = re.compile(r"/players/\w/(\w+)\.html")
_PLAYER_NAME = re.compile(r'data-stat="player"[^>]*>(?:<a[^>]*>)?([^<]+)')
_SALARY = re.compile(r'data-stat="salary"[^>]*>\s*\$?([\d,]+)')


def parse_team_salaries(html: str, bbref_code: str, season: str) -> list[SalaryRow]:
    """Extract the per-player salary table from a team-season page.

    Returns an empty list when the table is absent, which the caller must treat
    as a missing source rather than an empty team.
    """
    table = _SALARY_TABLE.search(unwrap(html))
    if not table:
        return []

    out: list[SalaryRow] = []
    for row in _ROW.findall(table.group(0)):
        pid = _PLAYER_ID.search(row)
        salary = _SALARY.search(row)
        name = _PLAYER_NAME.search(row)
        if not (pid and salary and name):
            continue
        out.append(
            SalaryRow(
                player_id=pid.group(1),
                name=name.group(1).strip(),
                team_id=TEAM_CODE[bbref_code],
                season=season,
                salary=int(salary.group(1).replace(",", "")),
            )
        )
    return out


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Transaction:
    season: str
    date: date
    text: str
    #: Same prose, but each player name followed by ``{{bbref_id}}``. Downstream
    #: clause parsing needs to know *which* player sits in which half of a
    #: sentence, and matching names back against a roster is unreliable —
    #: Basketball-Reference writes "Osasere Ighodaro" in transactions and "Oso
    #: Ighodaro" in salary tables. The anchors are unambiguous, so the ids are
    #: carried through from the markup instead of re-derived from the text.
    marked_text: str
    player_ids: tuple[str, ...]
    team_ids: tuple[str, ...]

    @property
    def is_trade(self) -> bool:
        return " traded " in self.text.lower()


_DATE_ITEM = re.compile(r'<li[^>]*>\s*<span[^>]*>(.*?)</span>(.*?)</li>', re.S)
_PARAGRAPH = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TEAM_LINK = re.compile(r"/teams/([A-Z]{3})/")

_MONTHS = {
    m: i
    for i, m in enumerate(
        "January February March April May June July August September "
        "October November December".split(),
        start=1,
    )
}


def _parse_date(raw: str) -> date | None:
    match = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text_of(raw))
    if not match or match.group(1) not in _MONTHS:
        return None
    return date(int(match.group(3)), _MONTHS[match.group(1)], int(match.group(2)))


def parse_transactions(html: str, season: str) -> list[Transaction]:
    """Extract one record per transaction.

    Basketball-Reference groups a whole day into one list item and puts each
    individual move in its own paragraph, so the date comes from the item and
    the transaction from the paragraph.
    """
    out: list[Transaction] = []
    for raw_date, body in _DATE_ITEM.findall(unwrap(html)):
        when = _parse_date(raw_date)
        if when is None:
            continue
        for paragraph in _PARAGRAPH.findall(body):
            description = text_of(paragraph)
            if not description:
                continue
            marked = text_of(
                re.sub(
                    r'<a[^>]*href="/players/\w/(\w+)\.html"[^>]*>(.*?)</a>',
                    lambda m: f"{m.group(2)}{{{{{m.group(1)}}}}}",
                    paragraph,
                    flags=re.S,
                )
            )
            out.append(
                Transaction(
                    season=season,
                    date=when,
                    text=description,
                    marked_text=marked,
                    player_ids=tuple(dict.fromkeys(_PLAYER_ID.findall(paragraph))),
                    team_ids=tuple(
                        dict.fromkeys(
                            TEAM_CODE[c] for c in _TEAM_LINK.findall(paragraph) if c in TEAM_CODE
                        )
                    ),
                )
            )
    return out


# --------------------------------------------------------------------------
# Contract structure
#
# A different page from the salary table, and a different *kind* of source.
# /teams/LAL/2025.html is a season archive: it will say what LAL paid in
# 2024-25 forever. /contracts/LAL.html is a live view of the contracts on the
# books right now, rewritten every year with no archive behind it.
#
# So contract structure can only ever be sourced for the current league year.
# It cannot be backfilled onto the 2023-24, 2024-25 or 2025-26 snapshots, and
# nothing here pretends otherwise — `end_year` for a historical season would
# have to be recalled rather than sourced, which is the one thing the charter
# rules out. See README, "Contract structure: what can and cannot be sourced".
# --------------------------------------------------------------------------


def team_contracts_url(bbref_code: str) -> str:
    return f"{BASE}/contracts/{bbref_code}.html"


#: Cell class -> what the annotation means. Basketball-Reference marks options
#: with a CSS class and non-guaranteed money with italics; there is no data
#: attribute for either, so the markup *is* the schema here.
OPTION_CLASS = {
    "salary-pl": "player_option",
    "salary-tm": "team_option",
    "salary-et": "early_termination",
}


@dataclass(frozen=True, slots=True)
class ContractYear:
    """One player-season of a contract currently on the books."""

    player_id: str
    name: str
    team_id: str
    season: str
    salary: int
    #: False when Basketball-Reference italicises the amount. Their legend
    #: reads "amount not fully guaranteed", so this is not the same as "$0
    #: guaranteed" and is deliberately not stored as a dollar figure.
    fully_guaranteed: bool
    #: "player_option" | "team_option" | "early_termination" | "" .
    option: str


_CONTRACT_TABLE = re.compile(r'<table[^>]*id="contracts".*?</table>', re.S)
_YEAR_HEADER = re.compile(
    r'data-stat="(y\d)"[^>]*data-over-header="Salary"[^>]*>(\d{4}-\d{2})<'
)
_YEAR_CELL = re.compile(
    r'<td class="([^"]*)" data-stat="(y\d)"(?: csk="(\d+)")?\s*>(.*?)</td>', re.S
)
_GTD_CELL = re.compile(r'data-stat="remain_gtd"(?: csk="(\d+)")?')


def parse_team_contracts(html: str, bbref_code: str) -> list[ContractYear]:
    """Every future player-season on this team's books, with its annotations.

    Returns an empty list when the table is absent, which the caller must treat
    as a missing source rather than a team with no contracts.
    """
    body = unwrap(html)
    table = _CONTRACT_TABLE.search(body)
    if not table:
        return []

    seasons = dict(_YEAR_HEADER.findall(body))
    if not seasons:
        return []

    out: list[ContractYear] = []
    for row in _ROW.findall(table.group(0)):
        pid = _PLAYER_ID.search(row)
        name = _PLAYER_NAME.search(row)
        if not (pid and name):
            continue
        for classes, column, csk, cell in _YEAR_CELL.findall(row):
            season = seasons.get(column)
            # "iz" is Basketball-Reference's empty-cell class: no salary that
            # year, which is what makes the last populated column the end year.
            if season is None or csk is None or not csk or "iz" in classes.split():
                continue
            option = next(
                (OPTION_CLASS[c] for c in classes.split() if c in OPTION_CLASS), ""
            )
            out.append(
                ContractYear(
                    player_id=pid.group(1),
                    name=name.group(1).strip(),
                    team_id=TEAM_CODE[bbref_code],
                    season=season,
                    salary=int(csk),
                    fully_guaranteed="<em>" not in cell,
                    option=option,
                )
            )
    return out


def contract_end_years(rows: list[ContractYear]) -> dict[tuple[str, str], str]:
    """``(player_id, team_id) -> last season with salary on the books``.

    Derived rather than parsed: Basketball-Reference publishes the years, not
    the end year, and computing it here keeps the one definition in one place.
    A player option in the final year still counts — the contract *can* run
    that long, and whether it does is exactly the uncertainty the option field
    is there to carry.
    """
    end: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row.player_id, row.team_id)
        if key not in end or row.season > end[key]:
            end[key] = row.season
    return end
