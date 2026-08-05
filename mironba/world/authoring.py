"""Plain-language scenario authoring: a sentence in, a DRAFT out, a human signs.

    python -m mironba.world.authoring "Stephen Curry traded to the Lakers"

The model proposes structure - kind, seed date, subjects, a stipulation or a
decision, scored teams. Everything after that is deterministic and everything
deterministic happens BEFORE anything is written:

* names resolve against the player table, with the hit rate reported the way
  every other join reports one;
* **ambiguity surfaces, never resolves silently.** "Curry" matches Stephen and
  Seth; both are in the data, and the sim has proposed Seth before. The
  resolver returns candidates and refuses to choose - a draft with an
  ambiguous name cannot validate until a human picks;
* teams must exist, the seed date must sit inside an ingested season;
* a stipulated package goes through ``rules/trade_validator.py`` with the
  findings shown when it fails.

The model never states a salary - the draft schema has no field to put one
in - and never writes a file. ``write_scenario`` is the only writer of
scenario yaml and it raises without ``confirmed=True``, which is a human act
at the CLI, exactly the contract the evidence store already enforces.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field

NL = chr(10)
from datetime import date
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "branch"


class AuthoringError(RuntimeError):
    """A draft tried to become a scenario without passing the gate."""


def _norm(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", text.lower()).strip()


def player_table() -> dict[str, str]:
    """player_id -> name, unioned across the ingested bbref player tables."""
    table: dict[str, str] = {}
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "players.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                table.setdefault(row["player_id"], row["name"])
    return table


def team_table() -> set[str]:
    return {code for candidates in team_index().values()
            for code in candidates}


#: Short forms the tables cannot supply, declared rather than guessed. "LA"
#: is genuinely ambiguous and must surface as a choice.
DECLARED_TEAM_ALIASES = {
    "la": ("LAC", "LAL"),          # genuinely ambiguous - surfaces as a choice
    "sixers": ("PHI",),            # table says 76ers
    "blazers": ("POR",),           # table says Trail Blazers
    "wolves": ("MIN",),
    "cavs": ("CLE",),
    "mavs": ("DAL",),
}


def team_index() -> dict:
    """norm(alias) -> set of team codes, from every ingested season's table.

    Aliases per team: the code, the city, the nickname, and city+nickname -
    so warriors / golden state / gsw / GSW all resolve to GSW. Built over
    the UNION of seasons so a relocation would appear as extra aliases (the
    ingested window carries none - measured, and reported by the hit-rate
    tool). Cities shared by two teams (Los Angeles) stay ambiguous by
    construction.
    """
    index: dict[str, set] = {}

    def add(alias: str, code: str) -> None:
        index.setdefault(_norm(alias), set()).add(code)

    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "teams.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                code = row["team_id"]
                add(code, code)
                add(row["city"], code)
                add(row["name"], code)
                add(row["city"] + " " + row["name"], code)
    for alias, codes in DECLARED_TEAM_ALIASES.items():
        index.setdefault(alias, set()).update(codes)
    return index


def team_names() -> dict:
    """code -> "City Nickname", newest season wins."""
    names: dict[str, str] = {}
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "teams.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                names[row["team_id"]] = f"{row['city']} {row['name']}"
    return names


def resolve_team(text: str, index: dict | None = None) -> list:
    """ALL candidates for a team string, as (code, full name) pairs.

    One candidate is a resolution; several is a question for the human -
    never a guess. An exact code match wins alone.
    """
    index = index if index is not None else team_index()
    names = team_names()
    key = _norm(text)
    if not key:
        return []
    codes = index.get(key, set())
    if text.strip().upper() in codes:
        return [(text.strip().upper(), names.get(text.strip().upper(), ""))]
    return sorted((c, names.get(c, "")) for c in codes)


def team_resolver_hit_rate() -> str:
    """The team join, measured like every other join: every known alias
    must resolve, and the genuinely ambiguous ones are named."""
    from mironba.data.joins import Join

    index = team_index()
    join = Join(name="team alias -> unique code", table={}, max_miss_rate=0.05)
    ambiguous = []
    for alias, codes in sorted(index.items()):
        join.total += 1
        if len(codes) == 1:
            join.matched += 1
        else:
            ambiguous.append(f"{alias} -> {sorted(codes)}")
    relocations = "none in the ingested window"
    return (join.report()
            + f"\n  ambiguous aliases (surface as a choice, never guessed): "
            + ("; ".join(ambiguous) if ambiguous else "none")
            + f"\n  relocations: {relocations}")


def ingested_window() -> tuple[date, date]:
    """The date range the snapshots can support a seed inside.

    Season directories (bbref-2025-26) cover their season; a forward contract
    snapshot (bbref-contracts-2026-27) prices packages one year further, which
    is exactly the year the July scenarios seed in.
    """
    starts = []
    for d in SNAPSHOTS.glob("bbref-*"):
        if not d.is_dir():
            continue
        m = re.search(r"(\d{4})-\d{2}$", d.name)
        if m:
            starts.append(int(m.group(1)))
    return date(min(starts), 7, 1), date(max(starts) + 1, 6, 30)


def resolve_name(name: str, players: dict[str, str]) -> list[tuple[str, str]]:
    """ALL candidates for a name. One is a resolution; several is a question.

    Exact normalised full-name match wins alone when it exists; otherwise any
    player whose name contains every word of the query is a candidate.
    """
    query = _norm(name)
    exact = [(pid, full) for pid, full in players.items() if _norm(full) == query]
    if exact:
        return sorted(exact)
    words = query.split()
    return sorted(
        (pid, full) for pid, full in players.items()
        if all(w in _norm(full).split() for w in words)
    )


def resolver_hit_rate() -> str:
    """The resolver measured like every other join: on the real name corpus.

    Full names must resolve uniquely; surnames are the ambiguity surface -
    report how many map to more than one player, because that is exactly the
    set where silent resolution would pick a wrong Curry.
    """
    from mironba.data.joins import Join

    players = player_table()
    join = Join(name="full name -> unique id", table={}, max_miss_rate=0.02)
    for pid, full in players.items():
        candidates = resolve_name(full, players)
        join.total += 1
        if len(candidates) == 1 and candidates[0][0] == pid:
            join.matched += 1
        elif len(join.missed_keys) < 20:
            join.missed_keys.append(f"{full} -> {[c[0] for c in candidates]}")
    surnames: dict[str, set[str]] = {}
    for pid, full in players.items():
        surnames.setdefault(_norm(full).split()[-1], set()).add(pid)
    ambiguous = sum(1 for ids in surnames.values() if len(ids) > 1)
    return (join.report()
            + f"\n  {'surname-only queries':<28} {ambiguous:>5}/{len(surnames):<5} "
              f"= {ambiguous / len(surnames):>5.1%} ambiguous (candidates surface, "
              "never auto-resolve)")


#: Every field the drafting model fills, audited against what the snapshot
#: can answer - the writer-enumeration move applied to world knowledge. The
#: from-team check caught the model asserting a decade-old fact against the
#: snapshot; fixing that one field and stopping would be fixing the writer
#: you tripped over. A test asserts this registry covers the Proposal
#: schema exactly, so a new field cannot ship unaudited.
WORLD_KNOWLEDGE_FIELDS = {
    "kind": "CONSTRAINED-AT-DECODE: Literal['stipulated','pending_decision']",
    "seed_date": "EXTRACTED-OR-RULED: taken from the sentence when stated; "
                 "otherwise defaulted by a DECLARED rule (July 6 after the "
                 "newest ingested season) with the rule named in the draft - "
                 "never invented. ISO-parsed; must sit inside the window.",
    "decision": "UNCHECKED-BY-DESIGN: free prose; the snapshot cannot answer "
                "a counterfactual's framing. Never feeds a computation.",
    "player_names": "CHECKED: resolver against the player table; ambiguity "
                    "surfaces, unknown names error",
    "team_codes": "RESOLVED like player names: codes, cities and "
                  "nicknames over every ingested season; genuine ambiguity "
                  "(la, los angeles) surfaces as a choice, never a guess",
    "moves.player_name": "CHECKED: resolver, same as player_names",
    "moves.from_team": "DERIVED from the contract snapshot when the "
                       "sentence does not state it; a model-supplied value "
                       "is only a CONFIRMATION against the snapshot (the "
                       "check that caught MIL for a player traded to MIA). "
                       "The snapshot decides.",
    "moves.to_team": "CHECKED: code must exist. The snapshot cannot check "
                     "the destination further - it IS the counterfactual.",
    "scored_teams": "CHECKED: codes must exist in the ingested team table "
                    "(was UNCHECKED until this audit; a wrong code passed "
                    "validation and would have bound TEAMS to garbage)",
}

# --------------------------------------------------------------------------
# The draft
# --------------------------------------------------------------------------


@dataclass
class Draft:
    """What the model proposed plus what determinism established about it.

    ``ambiguities`` maps a name to its candidate list; a non-empty map is a
    question for the human, and validation cannot pass while it has entries.
    """

    sentence: str
    kind: str = ""
    seed_date: str = ""
    decision: str = ""
    player_names: list = field(default_factory=list)
    team_codes: list = field(default_factory=list)
    moves: list = field(default_factory=list)   # {player_name, from, to}
    scored_teams: list = field(default_factory=list)
    resolved: dict = field(default_factory=dict)      # name -> player_id
    ambiguities: dict = field(default_factory=dict)   # name -> [(id, full)]
    errors: list = field(default_factory=list)
    findings: list = field(default_factory=list)      # validator output text
    #: What would resolve each error, index-aligned with ``errors``. A
    #: dead-end message in the primary entry point is the difference
    #: between a demo and a broken page.
    next_steps: list = field(default_factory=list)
    #: Legal return packages the solver enumerated for an under-specified
    #: trade: [{index, send: [names], salary_out, salary_in, headroom}].
    #: The model never sees these - it named a player, the solver priced
    #: the returns, and a human picks one by index.
    package_options: list = field(default_factory=list)
    chosen_package: int | None = None

    @property
    def awaiting_package(self) -> bool:
        """A solver choice is on the table and nobody has picked yet."""
        return bool(self.package_options) and self.chosen_package is None

    @property
    def ok(self) -> bool:
        return (not self.errors and not self.ambiguities
                and not self.awaiting_package)


def draft_from_sentence(sentence: str, client) -> Draft:
    """The model proposes structure. It cannot state a salary: no field exists.

    Returns an unvalidated Draft; ``validate_draft`` is where determinism
    takes over. Never writes anything anywhere. Two calls at most: the
    structure proposal, then - for a stipulated sentence whose one-shot
    moves came back empty - a tiny movements-only second call
    (``needs_moves_call``/``complete_moves`` expose the same two steps
    separately so the UI can show per-step progress).
    """
    from typing import Literal

    from pydantic import BaseModel, Field

    class Move(BaseModel):
        player_name: str
        from_team: str = Field(
            default="",
            description="ONLY if the sentence states it; leave blank "
                        "otherwise - the roster data knows where a player "
                        "is and the blank is derived, never guessed")
        to_team: str = Field(description="destination team, as written")

    class Proposal(BaseModel):
        kind: Literal["stipulated", "pending_decision"] = Field(
            description="'stipulated' if the sentence asserts an event; "
                        "'pending_decision' if something is unresolved")
        seed_date: str = Field(
            default="",
            description="ISO date ONLY if the sentence states one; leave "
                        "blank otherwise - an unstated date is defaulted by "
                        "a declared rule, never invented")
        decision: str = Field(description="one sentence: what is asserted or open")
        player_names: list[str] = Field(description="every player the sentence names")
        team_codes: list[str] = Field(description="every team involved, 3-letter codes")
        moves: list[Move] = Field(default_factory=list,
                                  description="stipulated trades only: who moves where")
        scored_teams: list[str] = Field(default_factory=list,
                                        description="teams to score; empty for stipulated")

    proposal = client.complete(
        [{"role": "user", "content":
          f"Turn this NBA counterfactual into a scenario structure.\n"
          f"Sentence: {sentence}\n"
          "Name only what the sentence names. Do not invent players or teams. "
          "For a stipulated trade, moves must list EVERY player movement the "
          "sentence states, in both directions. "
          "Never state salaries or contract figures - they are not yours to state."}],
        schema=Proposal, profile="authoring", purpose="scenario_draft",
    )
    # Step 2 (the tiny movements-only call for a stipulated sentence whose
    # one-shot moves came back empty - the charter's small-schema rule) is
    # NOT auto-run here: needs_moves_call/complete_moves own it, so the CLI
    # and the UI can each show it as its own step.
    moves = [m.model_dump() for m in proposal.moves]
    return Draft(
        sentence=sentence, kind=proposal.kind, seed_date=proposal.seed_date,
        decision=proposal.decision, player_names=list(proposal.player_names),
        team_codes=list(proposal.team_codes),
        moves=moves,
        scored_teams=list(proposal.scored_teams),
    )


def _fail(draft: Draft, message: str, next_step: str) -> None:
    """Every error carries what would resolve it."""
    draft.errors.append(message)
    draft.next_steps.append(next_step)


def choose_package(draft: Draft, index: int) -> Draft:
    """A human selects one of the solver's legal return packages by index."""
    if not 0 <= index < len(draft.package_options):
        raise AuthoringError(
            f"package {index} is not one of the {len(draft.package_options)} "
            "the solver offered")
    draft.chosen_package = index
    return draft


def needs_moves_call(draft: Draft) -> bool:
    """True when the tiny second LLM call (movements only) is still needed."""
    return draft.kind == "stipulated" and not draft.moves


def complete_moves(draft: Draft, client) -> Draft:
    """Step two of the draft flow, exposed for per-step UI progress."""
    from pydantic import BaseModel, Field

    class Move(BaseModel):
        player_name: str
        from_team: str = ""
        to_team: str = ""

    class Moves(BaseModel):
        moves: list[Move] = Field(
            description="every player movement the sentence states")

    prompt = NL.join((
        f"Sentence: {draft.sentence}",
        f"Players: {', '.join(draft.player_names)}. "
        f"Teams: {', '.join(draft.team_codes)}.",
        "List every player movement the sentence states, both "
        "directions. Leave from_team blank unless the sentence names it.",
    ))
    second = client.complete(
        [{"role": "user", "content": prompt}],
        schema=Moves, profile="authoring", purpose="scenario_draft_moves",
    )
    draft.moves = [m.model_dump() for m in second.moves]
    return draft


def validate_draft(draft: Draft) -> Draft:
    """Deterministic validation. Mutates and returns the draft.

    Order matters: resolution first, because a stipulated package cannot even
    be built while a name is ambiguous.
    """
    players = player_table()
    index = team_index()
    lo, hi = ingested_window()

    for name in draft.player_names + [m["player_name"] for m in draft.moves]:
        if name in draft.resolved or name in draft.ambiguities:
            continue
        candidates = resolve_name(name, players)
        if not candidates:
            _fail(draft, f"unresolved name: {name!r} matches nobody ingested",
                  f"check the spelling of {name!r}: it matched no player in "
                  "any ingested season's roster")
        elif len(candidates) == 1:
            draft.resolved[name] = candidates[0][0]
        else:
            draft.ambiguities[name] = candidates

    def team_of(text: str) -> str:
        """Resolve one team string; '' while unresolved or ambiguous."""
        if not text:
            return ""
        key = "team:" + text
        if key in draft.resolved:
            return draft.resolved[key]
        if key in draft.ambiguities:
            return ""
        candidates = resolve_team(text, index)
        if not candidates:
            _fail(draft, f"no such team: {text!r} (codes, cities and "
                         "nicknames all tried)",
                  f"name the team as a code, city or nickname - {text!r} "
                  "matched none of them")
            return ""
        if len(candidates) == 1:
            draft.resolved[key] = candidates[0][0]
            return candidates[0][0]
        draft.ambiguities[key] = candidates
        return ""

    draft.team_codes = [team_of(t) or t for t in draft.team_codes]
    draft.scored_teams = [team_of(t) or t for t in draft.scored_teams]
    for move in draft.moves:
        if move["from_team"]:
            move["from_team"] = team_of(move["from_team"]) or move["from_team"]
        move["to_team"] = team_of(move["to_team"]) or move["to_team"]

    if not draft.seed_date:
        newest = max(int(d.name.split("-")[1])
                     for d in SNAPSHOTS.glob("bbref-2*") if d.is_dir()
                     and not d.name.startswith("bbref-contracts"))
        draft.seed_date = f"{newest + 1}-07-06"
        draft.findings.append(
            f"seed_date defaulted by DECLARED RULE: July 6 after the newest "
            f"ingested season -> {draft.seed_date}. Stated, not invented; "
            "put a date in the sentence to override.")

    try:
        seed = date.fromisoformat(draft.seed_date)
        if not lo <= seed <= hi:
            _fail(draft, f"seed date {seed} is outside the ingested window "
                         f"{lo}..{hi}",
                  f"put a date between {lo} and {hi} in the sentence - the "
                  "snapshots cannot price a trade outside that window")
    except ValueError:
        _fail(draft, f"seed date {draft.seed_date!r} is not a date",
              "write the date as YYYY-MM-DD in the sentence, or leave it out "
              "and the declared rule supplies one")

    if draft.kind == "stipulated" and not draft.moves:
        _fail(draft, "stipulated but no moves declared",
              "name at least one player movement in the sentence, e.g. "
              "'<player> traded to <team>'")
    if draft.kind not in ("stipulated", "pending_decision"):
        _fail(draft, f"unknown kind {draft.kind!r}",
              "the kind must be 'stipulated' (an asserted event) or "
              "'pending_decision' (an open question)")

    if (draft.kind == "stipulated" and not draft.errors
            and not draft.ambiguities):
        _validate_package(draft)
    return draft


def _validate_package(draft: Draft) -> None:
    """The stipulated package through rules/, findings carried either way."""
    from mironba.rules.constants import environment_for
    from mironba.rules.trade_validator import (
        PlayerAsset, TeamTradeState, Trade, validate_trade,
    )

    seed = date.fromisoformat(draft.seed_date)
    season = f"{seed.year}-{str(seed.year + 1)[-2:]}" if seed.month >= 7 else \
             f"{seed.year - 1}-{str(seed.year)[-2:]}"
    contracts = SNAPSHOTS / f"bbref-contracts-{season}" / "contract_years.csv"
    if not contracts.is_file():
        _fail(draft, f"no contract snapshot for {season}; the package cannot "
                     "be priced",
              "seed the scenario in a season the contract ingest covers")
        return
    with contracts.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["season"] == season]
    salaries = {r["player_id"]: int(r["salary"]) for r in rows}
    payroll: dict[str, int] = {}
    roster: dict[str, set] = {}
    for r in rows:
        payroll[r["team_id"]] = payroll.get(r["team_id"], 0) + int(r["salary"])
        roster.setdefault(r["team_id"], set()).add(r["player_id"])

    players = []
    involved = set()
    for move in draft.moves:
        pid = draft.resolved[move["player_name"]]
        if pid not in salaries:
            _fail(draft, f"{move['player_name']} has no {season} contract "
                         "row; a draft cannot invent a salary",
                  f"{move['player_name']} is not under contract in {season} - "
                  "pick a season he is, or a different player")
            return
        snapshot_teams = sorted({r["team_id"] for r in rows
                                 if r["player_id"] == pid})
        if not move["from_team"]:
            # DERIVED, never asked: the snapshot knows where a player is at
            # the seed date. Multiple rows would surface as a choice; the
            # ingested snapshot carries one team per player per season.
            chosen = draft.resolved.get(f"team:from:{move['player_name']}")
            if chosen in snapshot_teams:
                move["from_team"] = chosen
            elif len(snapshot_teams) == 1:
                move["from_team"] = snapshot_teams[0]
                draft.findings.append(
                    f"from_team derived from the snapshot: "
                    f"{move['player_name']} is on {snapshot_teams[0]} "
                    "(the sentence did not say)")
            else:
                draft.ambiguities[f"team:from:{move['player_name']}"] = [
                    (t, t) for t in snapshot_teams]
                return
        elif move["from_team"] not in snapshot_teams:
            _fail(draft, f"confirmation failed: the sentence puts "
                         f"{move['player_name']} on {move['from_team']}, the "
                         f"{season} snapshot puts him on "
                         f"{'/'.join(snapshot_teams)} - the snapshot decides",
                  f"drop the from-team from the sentence and let the snapshot "
                  f"supply it ({'/'.join(snapshot_teams)})")
            return
        players.append(PlayerAsset(
            player_id=pid, name=move["player_name"], salary=salaries[pid],
            from_team=move["from_team"], to_team=move["to_team"]))
        involved.update((move["from_team"], move["to_team"]))

    # UNDER-SPECIFIED IS THE NORMAL CASE, not an error: "X traded to Y"
    # names an incoming player and no return. The model never states a
    # salary; the SOLVER enumerates legal returns and a human picks one -
    # the same boundary as the trade-intent loop.
    senders = {m["from_team"] for m in draft.moves}
    receivers = {m["to_team"] for m in draft.moves}
    one_sided = sorted(receivers - senders)
    if len(involved) == 2 and len(one_sided) == 1 and draft.chosen_package is None:
        destination = one_sided[0]
        source = next(t for t in involved if t != destination)
        incoming = [p for p in players if p.to_team == destination]
        options, refusal = _return_packages(
            destination, source, incoming, salaries, payroll, roster,
            season, seed)
        if refusal:
            _fail(draft, refusal["message"], refusal["next_step"])
            return
        draft.package_options = options
        draft.findings.append(
            f"the sentence names no return, so the SOLVER enumerated "
            f"{len(options)} legal package(s) {destination} could send for "
            + ", ".join(p.name for p in incoming)
            + " - the model never priced these; pick one.")
        return

    if draft.chosen_package is not None and draft.package_options:
        option = draft.package_options[draft.chosen_package]
        destination, source = option["destination"], option["source"]
        for name, pid in zip(option["send"], option["send_ids"]):
            draft.resolved.setdefault(name, pid)
            draft.moves.append({"player_name": name,
                                "from_team": destination, "to_team": source})
            players.append(PlayerAsset(
                player_id=pid, name=name, salary=salaries[pid],
                from_team=destination, to_team=source))
        draft.findings.append(
            f"return package chosen by you: {destination} sends "
            + ", ".join(option["send"])
            + f" (${option['salary_out']:,} out against "
              f"${option['salary_in']:,} in)")
        # consume the choice so a second validation cannot double-inject
        draft.package_options = []
        draft.chosen_package = None

    trade = Trade(
        season=season, trade_date=seed,
        teams=tuple(TeamTradeState(t, payroll.get(t, 0), len(roster.get(t, ())))
                    for t in sorted(involved)),
        players=tuple(players), label=draft.sentence)
    verdict = validate_trade(trade, environment_for(season))
    draft.findings.extend(str(f) for f in (getattr(verdict, "findings", []) or []))
    if not verdict.legal:
        errors = [str(f) for f in verdict.errors()]   # a method, not a property
        _fail(draft, "the stipulated package is not a legal trade; findings "
                     "above - declare a different package or accept that the "
                     "counterfactual is not constructible",
              "the rules refuse this package: "
              + (errors[0] if errors else "see the findings above")
              + ". Name a return in the sentence, or seed a different trade.")


def _return_packages(destination: str, source: str, incoming, salaries,
                     payroll, roster, season: str, seed):
    """Legal return packages the destination could send, from the solver.

    Returns (options, refusal). ``refusal`` is set when no legal package
    exists - which is a real and interesting answer, carrying the binding
    constraint quoted from rules/, not a failure of the flow.
    """
    from mironba.rules.solver import Asset, TradeIntent, solve
    from mironba.rules.trade_validator import TeamTradeState

    names = player_table()
    own = {pid: Asset(pid, names.get(pid, pid), salaries[pid])
           for pid in roster.get(destination, ()) if pid in salaries}
    theirs = {pid: Asset(pid, names.get(pid, pid), salaries[pid])
              for pid in roster.get(source, ()) if pid in salaries}
    if not own:
        return [], {"message": f"{destination} has no contracts to send back",
                    "next_step": "name a return in the sentence"}

    intent = TradeIntent(
        target_player_ids=tuple(p.player_id for p in incoming),
        tradeable_asset_ids=tuple(sorted(own, key=lambda p: own[p].salary)),
        priority=tuple(sorted(own, key=lambda p: own[p].salary)),
        rationale="under-specified stipulation: solver-enumerated return",
    )
    result = solve(
        intent, own=own, theirs=theirs,
        own_team=TeamTradeState(destination, payroll.get(destination, 0),
                                len(roster.get(destination, ()))),
        partner_team=TeamTradeState(source, payroll.get(source, 0),
                                    len(roster.get(source, ()))),
        season=season, trade_date=seed,
    )
    packages = list(getattr(result, "packages", None) or [])
    if not packages:
        binding = getattr(result, "binding_constraint", "") or "unknown"
        closest = getattr(result, "closest_miss", "") or ""
        return [], {
            "message": (f"NO LEGAL RETURN EXISTS: the solver searched every "
                        f"package {destination} could send for "
                        + ", ".join(p.name for p in incoming)
                        + f" and found none. Binding constraint: {binding}."
                        + (f" Closest miss: {closest}" if closest else "")),
            "next_step": ("that is a real answer, not a flow failure - this "
                          "counterfactual is not constructible under the "
                          f"{season} CBA as {destination} is currently "
                          "constructed. Seed a different destination or a "
                          "different season."),
        }

    options = []
    for i, package in enumerate(packages[:8]):
        options.append({
            "index": i, "destination": destination, "source": source,
            "send": [names.get(pid, pid) for pid in package.send_player_ids],
            "send_ids": list(package.send_player_ids),
            "salary_out": package.outgoing_salary,
            "salary_in": package.incoming_salary,
            "headroom": package.headroom,
            "verdict": getattr(package.verdict, "name", str(package.verdict)),
        })
    return options, None


def choose(draft: Draft, name: str, player_id: str) -> Draft:
    """A human resolves one ambiguity. The choice must be a listed candidate."""
    candidates = dict(draft.ambiguities.get(name, ()))
    if player_id not in candidates:
        raise AuthoringError(
            f"{player_id!r} is not a candidate for {name!r}: "
            f"{sorted(candidates)}")
    draft.resolved[name] = player_id
    del draft.ambiguities[name]
    return draft


def scenario_yaml(draft: Draft, scenario_id: str) -> str:
    if not draft.ok:
        raise AuthoringError("draft has open errors or ambiguities; not yamlable")
    seed = date.fromisoformat(draft.seed_date)
    # Project convention (see the curry yaml): `season` is the completed
    # season the snapshots describe; `next_season` is the league year the
    # event lands in. A July seed sits between the two.
    season = f"{seed.year - 1}-{str(seed.year)[-2:]}"
    next_season = (f"{seed.year}-{str(seed.year + 1)[-2:]}"
                   if seed.month >= 7 else season)
    lines = [
        f"id: {scenario_id}",
        f"kind: {draft.kind}",
        f'season: "{season}"',
        f"freeze: {draft.seed_date}",
        "freeze_rationale: >",
        f"  Drafted from: {draft.sentence!r}. The seed date was proposed by the",
        "  drafting model and confirmed by a human; state a better rationale",
        "  here before running anything that will be reported.",
        "subjects:",
    ]
    for name in draft.player_names:
        lines.append(f"  - {draft.resolved[name]}")
    for code in draft.team_codes:
        lines.append(f"  - {code}")
    lines.append(f'next_season: "{next_season}"')
    lines += ["decision: >", f"  {draft.decision}"]
    if draft.kind == "stipulated":
        lines += ["scored_teams: []", "stipulation:",
                  f"  label: {draft.sentence}", "  players:"]
        for move in draft.moves:
            lines += [f"    - player_id: {draft.resolved[move['player_name']]}",
                      f"      from: {move['from_team']}",
                      f"      to: {move['to_team']}"]
    else:
        lines.append("scored_teams:")
        for code in draft.scored_teams or draft.team_codes:
            lines.append(f"  - {code}")
    return chr(10).join(lines) + chr(10)


def write_scenario(draft: Draft, scenario_id: str, *, confirmed: bool = False,
                   config_dir: Path = CONFIG_DIR) -> Path:
    """THE only writer of scenario yaml, and it demands a human confirmation."""
    if not confirmed:
        raise AuthoringError(
            "a drafted scenario may not be written without explicit human "
            "confirmation. Review the draft and pass confirmed=True yourself - "
            "the phantom sixth suitor is what automatic acceptance produces."
        )
    if not draft.ok:
        raise AuthoringError(f"draft not clean: errors={draft.errors} "
                             f"ambiguities={sorted(draft.ambiguities)}")
    if draft.kind != "stipulated":
        raise AuthoringError(
            "authoring v0 writes stipulated scenarios only: a pending "
            "decision needs declared branches, premises and markers that a "
            "sentence does not carry, and the loader would refuse the file. "
            "Draft it here, then declare the branch structure by hand."
        )
    path = config_dir / f"{scenario_id}.yaml"
    if path.exists():
        raise AuthoringError(f"{path} already exists; authoring never overwrites")
    text = scenario_yaml(draft, scenario_id)
    # Round-trip BEFORE the write: the yaml must construct as a scenario or
    # nothing lands on disk. (The package bans deletion helpers - runs/ is
    # append-only - so write-then-rollback is not an available shape.)
    import yaml as _yaml

    from mironba.world.scenario import scenario_from_raw

    try:
        scenario_from_raw(_yaml.safe_load(text))
    except Exception as exc:
        raise AuthoringError(
            f"drafted yaml would not load as a scenario; nothing was "
            f"written: {exc}") from exc
    path.write_text(text, encoding="utf-8")
    return path


def render(draft: Draft) -> str:
    lines = [f"sentence: {draft.sentence}",
             f"proposed: kind={draft.kind} seed={draft.seed_date}",
             f"decision: {draft.decision}"]
    for name, pid in sorted(draft.resolved.items()):
        lines.append(f"  resolved   {name} -> {pid}")
    for name, candidates in sorted(draft.ambiguities.items()):
        lines.append(f"  AMBIGUOUS  {name!r} - a human must choose:")
        for pid, full in candidates:
            lines.append(f"      {pid}  {full}")
    for f in draft.findings:
        lines.append(f"  {f}")
    for e in draft.errors:
        lines.append(f"  ERROR  {e}")
    lines.append("valid, awaiting human confirmation" if draft.ok
                 else "NOT WRITABLE until the above is resolved")
    return chr(10).join(lines)


def _drafting_client():
    """A manifested client for drafting - same discipline as every LLM call."""
    from mironba.agents.report import template_hash
    from mironba.llm.client import (
        LLMClient, load_config, probe_model, probe_runtime, resolve_profile,
    )
    from mironba.world.manifest import Run, build_manifest

    config = load_config()
    cfg = resolve_profile(config, "authoring")
    info = probe_model(cfg)
    runtime = probe_runtime(cfg)
    manifest = build_manifest(
        model_id=cfg.model, server=cfg.server, base_url=cfg.base_url,
        quantization=info.quantization,
        prompt_template_hash=template_hash("scenario_draft_v1"),
        snapshot_date="n/a - drafting reads no snapshot",
        temperature=cfg.temperature, top_p=cfg.top_p, seed=cfg.seed,
        thinking=cfg.thinking, profile=cfg.name, scenario_id="authoring",
        model_size_bytes=runtime.size_bytes,
        model_size_vram_bytes=runtime.size_vram_bytes,
        gpu_fraction=runtime.gpu_fraction, fully_resident=runtime.fully_resident,
        notes="scenario authoring: a sentence becomes a DRAFT, never a file.",
    )
    return LLMClient(Run.start(manifest), config=config)


def main(argv=None) -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sentence")
    parser.add_argument("--choose", nargs=2, action="append", default=[],
                        metavar=("NAME", "PLAYER_ID"),
                        help="resolve one listed ambiguity; repeatable")
    parser.add_argument("--write", metavar="SCENARIO_ID", default="",
                        help="write the confirmed scenario file. Passing this "
                             "flag IS the human confirmation.")
    args = parser.parse_args(argv)

    client = _drafting_client()
    draft = draft_from_sentence(args.sentence, client)
    if needs_moves_call(draft):
        draft = complete_moves(draft, client)
    for name, pid in args.choose:
        try:
            validate_draft(draft)
            choose(draft, name, pid)
            draft.errors.clear()
            draft.findings.clear()
        except AuthoringError as exc:
            print(f"  ERROR {exc}")
            return 1
    validate_draft(draft)
    print(render(draft))
    print()
    print(resolver_hit_rate())
    print(team_resolver_hit_rate())
    if args.write:
        try:
            path = write_scenario(draft, args.write, confirmed=True)
            print(f"\nwrote {path} (confirmed at the CLI by --write)")
        except AuthoringError as exc:
            print(f"\nNOT WRITTEN: {exc}")
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
