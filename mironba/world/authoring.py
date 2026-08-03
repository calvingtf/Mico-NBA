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
    codes: set[str] = set()
    for directory in sorted(SNAPSHOTS.glob("bbref-2*")):
        path = directory / "teams.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as handle:
            codes.update(r["team_id"] for r in csv.DictReader(handle))
    return codes


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

    @property
    def ok(self) -> bool:
        return not self.errors and not self.ambiguities


def draft_from_sentence(sentence: str, client) -> Draft:
    """The model proposes structure. It cannot state a salary: no field exists.

    Returns an unvalidated Draft; ``validate_draft`` is where determinism
    takes over. Never writes anything anywhere.
    """
    from typing import Literal

    from pydantic import BaseModel, Field

    class Move(BaseModel):
        player_name: str
        from_team: str = Field(description="three-letter code")
        to_team: str = Field(description="three-letter code")

    class Proposal(BaseModel):
        kind: Literal["stipulated", "pending_decision"] = Field(
            description="'stipulated' if the sentence asserts an event; "
                        "'pending_decision' if something is unresolved")
        seed_date: str = Field(description="ISO date the scenario freezes at")
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
        schema=Proposal, profile="report_agent", purpose="scenario_draft",
    )
    moves = [m.model_dump() for m in proposal.moves]
    if proposal.kind == "stipulated" and not moves:
        # The charter's rule, hit live: a small model drifts on a nested
        # trade-in-one-shot and returns an empty list. Two-step it - the
        # second call asks ONLY for the movements, tiny schema, no nesting
        # beside it.
        class Moves(BaseModel):
            moves: list[Move] = Field(
                description="every player movement the sentence states")

        prompt = NL.join((
            f"Sentence: {sentence}",
            f"Players: {', '.join(proposal.player_names)}. "
            f"Teams: {', '.join(proposal.team_codes)}.",
            "List every player movement the sentence states, both "
            "directions. player names and 3-letter team codes only.",
        ))
        second = client.complete(
            [{"role": "user", "content": prompt}],
            schema=Moves, profile="report_agent", purpose="scenario_draft_moves",
        )
        moves = [m.model_dump() for m in second.moves]
    return Draft(
        sentence=sentence, kind=proposal.kind, seed_date=proposal.seed_date,
        decision=proposal.decision, player_names=list(proposal.player_names),
        team_codes=list(proposal.team_codes),
        moves=moves,
        scored_teams=list(proposal.scored_teams),
    )


def validate_draft(draft: Draft) -> Draft:
    """Deterministic validation. Mutates and returns the draft.

    Order matters: resolution first, because a stipulated package cannot even
    be built while a name is ambiguous.
    """
    players = player_table()
    teams = team_table()
    lo, hi = ingested_window()

    for name in draft.player_names + [m["player_name"] for m in draft.moves]:
        if name in draft.resolved or name in draft.ambiguities:
            continue
        candidates = resolve_name(name, players)
        if not candidates:
            draft.errors.append(f"unresolved name: {name!r} matches nobody ingested")
        elif len(candidates) == 1:
            draft.resolved[name] = candidates[0][0]
        else:
            draft.ambiguities[name] = candidates

    for code in set(draft.team_codes) | {m["from_team"] for m in draft.moves} | {
            m["to_team"] for m in draft.moves}:
        if code and code not in teams:
            draft.errors.append(f"no such team: {code!r}")

    try:
        seed = date.fromisoformat(draft.seed_date)
        if not lo <= seed <= hi:
            draft.errors.append(
                f"seed date {seed} is outside the ingested window {lo}..{hi}")
    except ValueError:
        draft.errors.append(f"seed date {draft.seed_date!r} is not a date")

    if draft.kind == "stipulated" and not draft.moves:
        draft.errors.append("stipulated but no moves declared")
    if draft.kind not in ("stipulated", "pending_decision"):
        draft.errors.append(f"unknown kind {draft.kind!r}")

    if draft.kind == "stipulated" and draft.ok:
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
        draft.errors.append(
            f"no contract snapshot for {season}; the package cannot be priced")
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
            draft.errors.append(
                f"{move['player_name']} has no {season} contract row; a draft "
                "cannot invent a salary")
            return
        on_team = next((r["team_id"] for r in rows if r["player_id"] == pid), "")
        if on_team != move["from_team"]:
            draft.errors.append(
                f"{move['player_name']} is on {on_team} in the {season} "
                f"snapshot, not {move['from_team']} - the draft may not move "
                "a player from a team the snapshot does not place him on")
            return
        players.append(PlayerAsset(
            player_id=pid, name=move["player_name"], salary=salaries[pid],
            from_team=move["from_team"], to_team=move["to_team"]))
        involved.update((move["from_team"], move["to_team"]))

    trade = Trade(
        season=season, trade_date=seed,
        teams=tuple(TeamTradeState(t, payroll.get(t, 0), len(roster.get(t, ())))
                    for t in sorted(involved)),
        players=tuple(players), label=draft.sentence)
    verdict = validate_trade(trade, environment_for(season))
    draft.findings = [str(f) for f in (getattr(verdict, "findings", []) or [])]
    if not verdict.legal:
        draft.errors.append(
            "the stipulated package is not a legal trade; findings above - "
            "declare a different package or accept that the counterfactual "
            "is not constructible")


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
    path = config_dir / f"{scenario_id}.yaml"
    if path.exists():
        raise AuthoringError(f"{path} already exists; authoring never overwrites")
    path.write_text(scenario_yaml(draft, scenario_id), encoding="utf-8")
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
    cfg = resolve_profile(config, "report_agent")
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

    draft = draft_from_sentence(args.sentence, _drafting_client())
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
