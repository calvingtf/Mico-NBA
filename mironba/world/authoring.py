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
import traceback
import unicodedata
from dataclasses import dataclass, field

NL = chr(10)
from datetime import date, datetime, timezone
from pathlib import Path

SNAPSHOTS = Path(__file__).resolve().parents[1] / "data" / "snapshots"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "branch"


class AuthoringCrash(RuntimeError):
    """The machinery broke. NOT a judgement about the scenario.

    Separate from ``AuthoringError`` because the two call for opposite
    responses from a reader: an AuthoringError says what to change about
    the scenario, and this says the check never ran. Collapsing them sends
    someone to edit a sentence that was never the problem.
    """

    def __init__(self, message: str, traceback: str = "") -> None:
        super().__init__(message)
        self.traceback = traceback


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
    "event": "CONSTRAINED-AT-DECODE: Literal['trade','signing'] - which "
             "KIND of stipulated event the sentence describes. The model "
             "classifies from the verb; the SNAPSHOT then overrules it, "
             "because whether a player was under contract at the freeze is "
             "a fact and not a reading. A 'signing' for a player on the "
             "same team in both season snapshots is refused and told to say "
             "'traded' instead - the same discipline as deriving from_team.",
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
    #: "trade" or "signing" - which KIND of stipulated event this is. The
    #: model classifies from the sentence; determinism then checks the
    #: classification against the snapshot, because whether a player is
    #: under contract is a fact and not a reading of the sentence.
    event: str = "trade"
    #: Legal signing routes the destination has, when event == "signing":
    #: [{route, max_first_year, max_years, raise_pct, hard_cap, describe}].
    signing_routes: list = field(default_factory=list)

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
        event: Literal["trade", "signing"] = Field(
            default="trade",
            description="'trade' if the sentence describes an EXCHANGE "
                        "between teams - players moving both ways, or a "
                        "verb like traded/dealt/swapped/acquired even when "
                        "only one side is named. 'signing' if a player "
                        "JOINS a team as a free agent with nothing going "
                        "back - verbs like signs/joins/agrees with. The "
                        "verb decides it: 'X traded to Y' is a trade even "
                        "though only one player is named.")
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
        moves=moves, event=proposal.event,
        scored_teams=list(proposal.scored_teams),
    )



#: Sentences whose event kind is not in doubt, with the answer. Used to
#: measure the classifier against a stated null - a balanced set, so
#: always-answer-"trade" scores exactly half and any classifier has to beat
#: that to have said anything.
CLASSIFIER_SET = (
    ("Stephen Curry traded from GSW to LAL for Austin Reaves and Quentin "
     "Grimes on 2026-07-06", "trade"),
    ("Victor Wembanyama traded to the Warriors", "trade"),
    ("Giannis Antetokounmpo traded from MIA to NYK for Karl-Anthony Towns",
     "trade"),
    ("The Lakers deal Rui Hachimura to Chicago for Nikola Vucevic", "trade"),
    ("Anthony Davis is swapped to Boston for Jaylen Brown", "trade"),
    ("Golden State acquires Zach LaVine from Sacramento", "trade"),
    ("LeBron James signs with the Golden State Warriors", "signing"),
    ("DeMar DeRozan signs a two-year deal with the Knicks", "signing"),
    ("Paul George joins the Nets in free agency", "signing"),
    ("Terry Rozier agrees to terms with Portland", "signing"),
    ("Kentavious Caldwell-Pope signs with Orlando", "signing"),
    ("Jonas Valanciunas inks a deal with the Spurs", "signing"),
)


def classify_event(sentence: str, client) -> str:
    """Trade or signing, as its OWN call with a one-field schema.

    Measured, not assumed, on the balanced set in ``CLASSIFIER_SET``
    (entry #74):

        null, always "trade"          6 / 12
        field in the full schema      6 / 12   said "signing" 0 times
        this call                    12 / 12   median 49s vs 176s

    Folded into the main proposal schema the field scored exactly the null
    and never emitted "signing" once in twelve sentences - it was not
    classifying, it was falling through to the default, the same
    under-filling that leaves ``moves`` empty and is why ``complete_moves``
    exists. A field that can only return one of its two values is not a
    classifier, and accuracy alone would not have shown that.

    The charter's rule for this is to shrink the schema rather than to
    prompt harder, so the classification is asked on its own with nothing
    else to fill in. That is 3.6x faster as well as correct: same model,
    same machine, less to emit. ``event`` on the Proposal is kept as a
    first guess and this overrides it.
    """
    from typing import Literal

    from pydantic import BaseModel, Field

    class EventKind(BaseModel):
        event: Literal["trade", "signing"] = Field(
            description="'trade' if players are EXCHANGED between teams - "
                        "traded, dealt, swapped, acquired - even when only "
                        "one side is named. 'signing' if a free agent JOINS "
                        "a team with nothing going back - signs, joins, "
                        "agrees to terms, inks a deal.")

    answer = client.complete(
        [{"role": "user", "content":
          "Does this sentence describe a TRADE (an exchange between teams) "
          "or a SIGNING (a free agent joining a team, nothing going back)?"
          f"\n\nSentence: {sentence}"}],
        schema=EventKind, profile="authoring", purpose="event_classification",
    )
    return answer.event



#: The OTHER classifier in the proposal schema, with its own balanced set.
#: Declared before it was measured, so the set could not be chosen to suit
#: the answer. Six of each, so always-answer-"stipulated" scores 6/12.
KIND_SET = (
    ("Stephen Curry traded from GSW to LAL for Austin Reaves and Quentin "
     "Grimes", "stipulated"),
    ("LeBron James signs with the Golden State Warriors", "stipulated"),
    ("Victor Wembanyama traded to the Warriors", "stipulated"),
    ("Giannis Antetokounmpo traded from MIA to NYK for Karl-Anthony Towns",
     "stipulated"),
    ("Paul George joins the Nets in free agency", "stipulated"),
    ("The Lakers deal Rui Hachimura to Chicago for Nikola Vucevic",
     "stipulated"),
    ("Where does LeBron James sign this offseason?", "pending_decision"),
    ("Will Giannis Antetokounmpo be traded before the deadline?",
     "pending_decision"),
    ("Does Paul George opt in or test free agency?", "pending_decision"),
    ("Who wins the race to sign DeMar DeRozan?", "pending_decision"),
    ("Whether the Lakers extend Austin Reaves is still open.",
     "pending_decision"),
    ("It is undecided whether Terry Rozier stays in Charlotte.",
     "pending_decision"),
)


def classify_kind(sentence: str, client) -> str:
    """Stipulated or pending decision, as its OWN one-field call.

    NOT wired into the drafting flow, and MEASURED SO (#77):

        null, always "stipulated"        6 / 12
        `kind` in the full schema       12 / 12   both classes emitted, 80s
        this call                       12 / 12   both classes emitted, 18s

    The field is not inert inside the large schema. It is perfect there, and
    this call buys nothing but a round trip - so the flow keeps using the
    schema field and this function stays as the measured-and-rejected arm,
    the same way ``authoring_nothink`` is kept in models.yaml.

    That is the more useful half of #74. A field inside a large schema *can*
    be inert; it is not inert *because* the schema is large. `event` and
    `kind` sit in the same eight-field schema, are both Literal classifiers
    of the same shape, and one was a constant while the other was flawless.
    Schema size says where to look, never what you will find - which is why
    the rule is "measure one candidate", not "split multi-field calls".
    """
    from typing import Literal

    from pydantic import BaseModel, Field

    class ScenarioKind(BaseModel):
        kind: Literal["stipulated", "pending_decision"] = Field(
            description="'stipulated' if the sentence ASSERTS that something "
                        "happened - it states an event as fact. "
                        "'pending_decision' if the sentence poses an OPEN "
                        "QUESTION - it asks what will happen, or says "
                        "something is undecided or still open.")

    answer = client.complete(
        [{"role": "user", "content":
          "Does this sentence ASSERT an event as fact (stipulated), or pose "
          "an OPEN QUESTION about something not yet decided "
          f"(pending_decision)?\n\nSentence: {sentence}"}],
        schema=ScenarioKind, profile="authoring",
        purpose="kind_classification_measurement",
    )
    return answer.kind


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


def needs_event_call(draft: Draft) -> bool:
    """True while the event kind is still the schema's default guess."""
    return draft.kind == "stipulated"


def complete_event(draft: Draft, client) -> Draft:
    """Classify trade-vs-signing on its own, and record what changed."""
    guessed = draft.event
    draft.event = classify_event(draft.sentence, client)
    if draft.event != guessed:
        draft.findings.append(
            f"event kind reclassified {guessed!r} -> {draft.event!r} by the "
            "dedicated one-field call; the combined schema is measurably "
            "unreliable on this field")
    return draft


def needs_moves_call(draft: Draft) -> bool:
    """True when the tiny second LLM call (movements only) is still needed.

    Signings need it as much as trades: the one-shot returned empty moves
    for three of the first four sentences measured, whichever kind they
    were, and a signing with no movement has no player and no destination.
    """
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


def validate_draft(draft: Draft, on_step=None) -> Draft:
    """Deterministic validation. Mutates and returns the draft.

    Order matters: resolution first, because a stipulated package cannot even
    be built while a name is ambiguous.

    ``on_step(name, detail)`` is called as each phase completes, so a caller
    can stream progress. It changes nothing about the validation: a UI that
    watches must not be able to steer.
    """
    def step(name: str, detail: str = "") -> None:
        if on_step is not None:
            on_step(name, detail)
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

    step("players resolved",
         ", ".join(f"{k} -> {v}" for k, v in draft.resolved.items()
                   if not k.startswith("team:"))
         or "no player names to resolve")

    draft.team_codes = [team_of(t) or t for t in draft.team_codes]
    draft.scored_teams = [team_of(t) or t for t in draft.scored_teams]
    for move in draft.moves:
        if move["from_team"]:
            move["from_team"] = team_of(move["from_team"]) or move["from_team"]
        move["to_team"] = team_of(move["to_team"]) or move["to_team"]

    step("teams resolved",
         ", ".join(f"{k.replace('team:', '')} -> {v}"
                   for k, v in draft.resolved.items() if k.startswith("team:"))
         or "no team names to resolve")

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

    step("seed date", draft.seed_date)

    if (draft.kind == "stipulated" and not draft.errors
            and not draft.ambiguities):
        if draft.event == "signing":
            _validate_signing(draft, on_step=on_step)
        else:
            _validate_package(draft, on_step=on_step)
    return draft



def _prior_season_team(pid: str, season: str) -> str:
    """Which team held this player the season BEFORE ``season``.

    Empty when the prior snapshot has no row for him. Mirrors the
    denominator of ``LeagueState.arrivals`` so authoring and the runner
    answer "was he signable at the freeze" the same way.
    """
    start = int(season[:4]) - 1
    prior = f"{start}-{str(start + 1)[-2:]}"
    # The SAME file LeagueState.load reads for team_2526. There is exactly
    # one contract-years snapshot and it starts at the target season, so
    # looking for prior-season rows there finds nothing and silently makes
    # every player look like an arrival - which passed Stephen Curry, who is
    # on Golden State in both seasons, as a free agent. The prior season
    # lives in its own directory as a roster-level contracts file.
    path = SNAPSHOTS / f"bbref-{prior}" / "contracts.csv"
    if not path.is_file():
        return ""
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["player_id"] == pid:
                return row["team_id"]
    return ""

    when = signings.get(pid)
    if when is not None and when > freeze:
        return when
    return None

def _validate_signing(draft: Draft, on_step=None) -> None:
    """A stipulated SIGNING through rules/signing.py, not the trade validator.

    The trade validator has nothing to say here: no counterparty, no salary
    matching, no aggregation. The question is whether the destination has a
    legal ROUTE on the seed date, and rules/signing_solver.feasible_signings
    plus rules/signing.signing_routes answer it. Same boundary as the trade
    path - the model names a player and a team and never an amount.
    """
    def step(name: str, detail: str = "") -> None:
        if on_step is not None:
            on_step(name, detail)

    from mironba.rules.constants import environment_for
    from mironba.rules.signing import FreeAgent, TeamCapState, signing_routes
    from mironba.rules.signing_solver import feasible_signings

    if len(draft.moves) != 1:
        _fail(draft, f"a signing names one player joining one team; this "
                     f"sentence produced {len(draft.moves)} movements",
              "state one player and one destination, or describe it as a "
              "trade if players move both ways")
        return
    move = draft.moves[0]
    pid = draft.resolved.get(move["player_name"], "")
    destination = move["to_team"]
    if not pid or not destination:
        _fail(draft, "a signing needs a resolved player and a destination",
              "name the player and the team he joins")
        return

    seed = date.fromisoformat(draft.seed_date)
    season = f"{seed.year}-{str(seed.year + 1)[-2:]}" if seed.month >= 7 else \
             f"{seed.year - 1}-{str(seed.year)[-2:]}"
    contracts = SNAPSHOTS / f"bbref-contracts-{season}" / "contract_years.csv"
    if not contracts.is_file():
        _fail(draft, f"no contract snapshot for {season}; the signing cannot "
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

    # The snapshot decides whether this is a signing at all - but it has to
    # be asked AS OF THE FREEZE, and the two layers must agree about it.
    #
    # The contract file is an end-of-season artifact: it records that LeBron
    # James holds a 2026-27 deal with Philadelphia. He was not under it on
    # the freeze date. Refusing the stipulation on the row alone would use
    # post-freeze information to rule out a counterfactual set AT the
    # freeze - the same leak class the ranker corrections were about,
    # arriving through a gate rather than through a feature. The first
    # version of this check did that and refused every signing worth
    # writing.
    #
    # The runner's own rule is ``LeagueState.arrivals``: on this team in the
    # target season and NOT on it in the prior one. That is computable here
    # from the two snapshots, so authoring applies the same definition
    # rather than a stricter one of its own - two layers disagreeing about
    # what is signable is worse than either answer.
    if pid in salaries:
        holder = next((t for t, ids in roster.items() if pid in ids), "?")
        prior_team = _prior_season_team(pid, season)
        if prior_team == holder or not prior_team:
            _fail(draft,
                  f"{move['player_name']} is under contract with {holder} "
                  f"for {season} and "
                  + (f"was on {holder} the season before"
                     if prior_team else
                     "has no prior-season row to show he changed teams")
                  + "; a signing cannot be stipulated for a player who is "
                    "not a free agent",
                  f"describe this as a trade instead - "
                  f"'{move['player_name']} traded to {destination}' - and "
                  "the solver will price the return")
            return
        draft.findings.append(
            f"{move['player_name']} appears in the {season} contract file "
            f"with {holder} but was on {prior_team or 'no ingested team'} "
            f"the season before, so that deal is an arrival the freeze date "
            f"({draft.seed_date}) precedes. At the freeze he was signable, "
            "which is the same rule the reaction uses to build its pool")
        salaries = {k: v for k, v in salaries.items() if k != pid}
        roster[holder] = {x for x in roster.get(holder, set()) if x != pid}

    env = environment_for(season)
    names = player_table()
    state = TeamCapState(team_id=destination, season=season,
                         committed_salary=payroll.get(destination, 0),
                         roster_count=len(roster.get(destination, ())))
    # Years of service and prior salary drive the Bird and max-salary tiers.
    # The authoring snapshot carries neither for a player with no row in the
    # target season, and inventing them would put a number in the model's
    # mouth by another door. Both are left at the conservative floor and the
    # finding says so: the routes below are therefore a LOWER bound on what
    # the destination could offer, never an upper one.
    agent = FreeAgent(player_id=pid, name=names.get(pid, pid),
                      years_of_service=0, prior_salary=0, years_with_team=0)
    draft.findings.append(
        "years of service and prior salary are not in the authoring "
        "snapshot for a player with no contract row, so both are held at "
        "zero; the routes below are a LOWER bound on the terms available "
        "and the run itself uses the full league state")
    step("signing solver enumerating routes",
         f"{destination} on {draft.seed_date}")
    scan = feasible_signings(state, [agent], env)
    result = signing_routes(state, agent, env)
    draft.signing_routes = [
        {"route": r.route, "max_first_year": r.max_first_year,
         "max_years": r.max_years, "raise_pct": r.raise_pct,
         "hard_cap": r.hard_cap, "describe": r.describe()}
        for r in result.routes
    ]
    step("signing solver finished",
         f"{len(result.routes)} legal route(s)")
    if result.routes:
        draft.findings.extend(r.describe() for r in result.routes)
        draft.findings.append(
            f"the sentence states no amount and no route; the run will use "
            f"the best legal route ({result.best().route} at "
            f"${result.best().max_first_year:,}) and record that the figure "
            "was derived, not declared")
        return
    reason = "; ".join(f"{route}: {why}"
                       for route, why in sorted(result.blocked.items()))
    if not reason:
        reason = scan.empty_reason or "no route and no reason - a bug"
    _fail(draft,
          f"NO LEGAL ROUTE: {destination} cannot sign "
          f"{move['player_name']} on {draft.seed_date}. {reason}",
          "that is a real answer, not a flow failure - this counterfactual "
          "is not constructible under the 2023 CBA at this date. Name a "
          "different destination, or accept the refusal")


def _validate_package(draft: Draft, on_step=None) -> None:
    """The stipulated package through rules/, findings carried either way."""
    def step(name: str, detail: str = "") -> None:
        if on_step is not None:
            on_step(name, detail)
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
        step("solver enumerating returns",
             f"{destination} must send salary back for "
             + ", ".join(p.name for p in incoming))
        options, refusal = _return_packages(
            destination, source, incoming, salaries, payroll, roster,
            season, seed)
        step("solver finished",
             f"{len(options)} legal package(s)" if options
             else "no legal package exists")
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
    step("rules verdict",
         "LEGAL" if verdict.verdict.name == "APPROVED" else verdict.verdict.name)
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



def team_nicknames() -> dict:
    """code -> nickname ("LAL" -> "lakers"), from the ingested team tables."""
    out: dict[str, str] = {}
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "teams.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                out.setdefault(row["team_id"], _slug_part(row["name"]))
    return out


def _slug_part(text: str) -> str:
    """One slug component: ascii, lowercase, hyphen-separated, no empties.

    SLUG RULES, stated once and applied everywhere:

    * unicode is folded to ascii (Porziņģis -> porzingis), because a
      filename is a path and a path is not a place for combining marks;
    * everything that is not a letter or digit becomes a separator;
    * runs of separators collapse and the ends are trimmed;
    * the result is lowercased.

    Never returns something that could be read as a number by yaml on its
    own - the caller always joins at least one alphabetic component - and
    never returns an empty string to the caller: ``derive_scenario_id``
    drops empty parts and falls back rather than emitting "--2026".
    """
    folded = unicodedata.normalize("NFKD", str(text))
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-",
                                     ascii_only.lower())).strip("-")


def derive_scenario_id(draft: Draft, taken=None) -> str:
    """A scenario id from the RESOLVED content. The user never types one.

    ``<subject>-to-<destination>-<season year>`` - "curry-to-lakers-2026".
    The subject is the first moving player's surname, the destination the
    team he arrives at (nickname where the ingest knows one, code
    otherwise), the year the seed date's.

    Always a non-empty string and never numeric-looking, because it is used
    as a filename and as a path component and because an id that yaml can
    reparse as an int crashes the first join that touches it.

    ``taken`` is an iterable of ids already in use: a collision gets ``-2``,
    ``-3`` and so on rather than overwriting. ``write_scenario`` still
    refuses to overwrite regardless - this only stops the refusal from
    being the user's first news of the clash.
    """
    move = draft.moves[0] if draft.moves else {}
    name = str(move.get("player_name") or
               (draft.player_names[0] if draft.player_names else ""))
    surname = _slug_part(name.split()[-1]) if name.split() else ""
    destination = str(move.get("to_team") or
                      (draft.team_codes[0] if draft.team_codes else ""))
    nick = team_nicknames().get(destination, "")
    where = nick or _slug_part(destination)
    year = ""
    if draft.seed_date:
        year = _slug_part(str(draft.seed_date).split("-")[0])

    parts = [p for p in (surname, "to", where, year) if p and p != "to"]
    if surname and where:
        parts = [surname, "to", where] + ([year] if year else [])
    base = "-".join(parts) if parts else ""
    if not base or not any(ch.isalpha() for ch in base):
        # Nothing usable resolved. A timestamp is ugly and unambiguous,
        # which beats an id that yaml will turn back into a number.
        base = "scenario-" + datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%SZ")
    taken = set(taken or ())
    if base not in taken:
        return base
    for n in range(2, 1000):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    return f"{base}-{datetime.now(timezone.utc).strftime('%H%M%S')}"


def existing_scenario_ids(config_dir: Path = CONFIG_DIR) -> set:
    """Ids already declared, so a derived one can step around them."""
    return {path.stem for path in config_dir.glob("*.yaml")}


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
        # QUOTED. Unquoted, `id: 2026` round-trips out of yaml as an int and
        # crashes the first path join that uses it. Quoting is the fix at
        # the writing end; scenario.py checks the type at the reading end.
        f'id: "{scenario_id}"',
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
    # decision_subject is deliberately NOT emitted. run_branch removes
    # SUBJECT from the signable pool, so naming a stipulated signee there
    # would remove him from the run WITHOUT the seed as well and the
    # comparison would silently come back empty - see
    # sim/signing_seed.build_signing, which refuses that configuration.
    if draft.kind == "stipulated" and draft.event == "signing":
        move = draft.moves[0]
        lines += ["scored_teams: []", "stipulation:",
                  f"  label: {draft.sentence}",
                  "  # No salary and no route are declared. The signing "
                  "solver enumerates every",
                  "  # legal route and the runner records which it used and "
                  "whether that choice",
                  "  # was this file's or the solver's.",
                  "  signing:",
                  f"    player_id: {draft.resolved[move['player_name']]}",
                  f"    to: {move['to_team']}"]
    elif draft.kind == "stipulated":
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

    # A REJECTION AND A CRASH ARE DIFFERENT ANSWERS.
    #
    # ScenarioError means the loader looked at the file and judged it: the
    # message says what is wrong and what would fix it, and "drafted yaml
    # would not load as a scenario" is the right thing to say.
    #
    # Anything else means the loader BROKE. It did not judge the scenario,
    # so reporting a verdict would be a claim nobody made - and it hid a
    # real bug for exactly that reason: an unquoted numeric id raised
    # "unsupported operand type(s) for /: 'WindowsPath' and 'int'" and the
    # blanket handler dressed it up as a validation failure, sending anyone
    # reading it to look at their sentence instead of at the path join.
    from mironba.world.scenario import ScenarioError

    try:
        scenario_from_raw(_yaml.safe_load(text))
    except ScenarioError as exc:
        raise AuthoringError(
            f"drafted yaml would not load as a scenario; nothing was "
            f"written: {exc}") from exc
    except Exception as exc:
        raise AuthoringCrash(
            f"the scenario loader crashed while checking the drafted yaml; "
            f"nothing was written. This is NOT a verdict on the scenario - "
            f"it was never judged. {type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        ) from exc
    path.write_text(text, encoding="utf-8")
    return path


def render(draft: Draft) -> str:
    lines = [f"sentence: {draft.sentence}",
             f"proposed: kind={draft.kind} event={draft.event} "
             f"seed={draft.seed_date}",
             f"decision: {draft.decision}"]
    for route in draft.signing_routes:
        lines.append(f"  route      {route['describe']}")
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
    if needs_event_call(draft):
        draft = complete_event(draft, client)
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
