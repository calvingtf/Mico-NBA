"""Deterministic package search. No LLM, no salaries crossing the boundary.

M1 measured a propose-then-validate loop at roughly zero legal proposals across
twelve live attempts, and nine repair retries rescued none of them. The model
responded to each rejection — dropped players, shrank packages, tried to fix
roster counts — and still could not solve apron salary matching, which is a
constraint-satisfaction problem over integers, not a language problem.

The response is not a better prompt. It is to stop asking. An LLM states *what
it wants*; this module works out *what is legal*; the LLM then picks from legal
options. Illegal packages become unrepresentable rather than merely discouraged,
because there is no code path by which one reaches the validator.

The search is subset selection over the acquiring team's tradeable contracts,
pruned on the salary-matching bound before anything is fully validated. Every
package returned has been through ``validate_trade`` and carries no ERROR
finding — the solver never re-implements a rule, it only avoids proposing
things the validator would reject. That is what keeps the two from disagreeing:
``test_solver_and_validator_never_disagree`` asserts it over generated intents.

UNDETERMINED is treated as legal-so-far, deliberately. Base-year compensation
is an unknown, not an illegality, and a snapshot-derived trade is UNDETERMINED
for every player. Excluding those would make the solver return nothing on real
data and would hide the one verdict M0 built a third value for.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Mapping

from mironba.rules.cap import max_incoming_salary
from mironba.rules.constants import CapEnvironment, environment_for
from mironba.rules.trade_validator import (
    PlayerAsset,
    ReSignStatus,
    Severity,
    TeamTradeState,
    Trade,
    Verdict,
    validate_trade,
)

#: Hard ceiling on how many contracts may be combined in one package. Four is
#: already generous for a single trade and keeps the search space at C(n,<=4).
#: The real limit is usually lower — a persona cap, or the second apron's
#: outright aggregation ban.
MAX_ASSETS_OUT = 4

#: How many packages to return. More than a handful is not a choice, it is a
#: list, and the point is to give a model something it can actually weigh.
DEFAULT_LIMIT = 10


@dataclass(frozen=True, slots=True)
class Asset:
    """A contract the solver may move. Salary comes from the snapshot."""

    player_id: str
    name: str
    salary: int


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """What a GM wants, stated without any package or any salary.

    This is the entire vocabulary an LLM gets. It cannot name a dollar figure,
    cannot assemble a package, and cannot assert legality — those are the three
    things it demonstrably cannot do reliably.
    """

    #: Players to acquire, from the counterparty's roster.
    target_player_ids: tuple[str, ...]
    #: Own contracts the GM is willing to part with. The solver searches
    #: subsets of exactly this set.
    tradeable_asset_ids: tuple[str, ...]
    #: Never send these, whatever the arithmetic says. Applied after
    #: ``tradeable_asset_ids`` so a contradictory intent resolves to exclusion.
    excluded_player_ids: tuple[str, ...] = ()
    #: Tradeable ids, most expendable first. Used only to order equally legal
    #: packages, so the ranking reflects the GM's stated preference rather than
    #: an accident of iteration order.
    priority: tuple[str, ...] = ()
    rationale: str = ""

    def eligible(self, own: Mapping[str, Asset]) -> list[str]:
        excluded = set(self.excluded_player_ids)
        seen: set[str] = set()
        out = []
        for pid in self.tradeable_asset_ids:
            if pid in own and pid not in excluded and pid not in seen:
                seen.add(pid)
                out.append(pid)
        return out


@dataclass(frozen=True, slots=True)
class Package:
    """One legal way to satisfy an intent."""

    send_player_ids: tuple[str, ...]
    receive_player_ids: tuple[str, ...]
    outgoing_salary: int
    incoming_salary: int
    headroom: int
    verdict: Verdict
    notes: tuple[str, ...] = ()

    def describe(self, own: Mapping[str, Asset], theirs: Mapping[str, Asset]) -> str:
        out = ", ".join(own[p].name for p in self.send_player_ids)
        inc = ", ".join(theirs[p].name for p in self.receive_player_ids)
        return f"send {out} -> receive {inc}"


@dataclass
class SolverResult:
    """Legal packages, or an explicit account of why there are none."""

    packages: list[Package] = field(default_factory=list)
    #: Subsets that survived the cheap bound and were fully validated.
    examined: int = 0
    #: Legal packages found *before* the return cap was applied.
    feasible_found: int = 0
    truncated: bool = False
    binding_constraint: str | None = None
    blocking_rules: dict[str, int] = field(default_factory=dict)
    closest_miss: str = ""
    elapsed_s: float = 0.0

    @property
    def satisfiable(self) -> bool:
        return bool(self.packages)

    def explain(self) -> str:
        if self.packages:
            head = f"{self.feasible_found} legal package(s)"
            if self.truncated:
                head += f", showing {len(self.packages)}"
            return f"{head} from {self.examined} candidates in {self.elapsed_s:.3f}s"
        return (
            f"no legal package. Binding constraint: {self.binding_constraint}. "
            + (f"Closest miss: {self.closest_miss}. " if self.closest_miss else "")
            + f"({self.examined} candidates in {self.elapsed_s:.3f}s)"
        )


def build_trade(
    send_player_ids: tuple[str, ...],
    receive_player_ids: tuple[str, ...],
    *,
    own: Mapping[str, Asset],
    theirs: Mapping[str, Asset],
    own_team: TeamTradeState,
    partner_team: TeamTradeState,
    season: str,
    trade_date: date,
    re_sign_status: ReSignStatus = ReSignStatus.UNKNOWN,
) -> Trade:
    """Assemble the ``Trade`` for a package. The only place that does it.

    Public so the caller that judges a *selected* package builds exactly the
    same object the solver validated. Two construction sites would eventually
    disagree about some detail — a date, a re-sign status — and the solver's
    guarantee would quietly stop meaning anything.
    """
    return Trade(
        season=season,
        trade_date=trade_date,
        teams=(own_team, partner_team),
        players=tuple(
            PlayerAsset(
                player_id=pid,
                name=own[pid].name,
                salary=own[pid].salary,       # from the snapshot, never a model
                from_team=own_team.team_id,
                to_team=partner_team.team_id,
                re_sign_status=re_sign_status,
            )
            for pid in send_player_ids
        )
        + tuple(
            PlayerAsset(
                player_id=pid,
                name=theirs[pid].name,
                salary=theirs[pid].salary,    # from the snapshot, never a model
                from_team=partner_team.team_id,
                to_team=own_team.team_id,
                re_sign_status=re_sign_status,
            )
            for pid in receive_player_ids
        ),
    )


def _rank(intent: TradeIntent) -> dict[str, int]:
    order = {pid: i for i, pid in enumerate(intent.priority)}
    return order


def solve(
    intent: TradeIntent,
    *,
    own: Mapping[str, Asset],
    theirs: Mapping[str, Asset],
    own_team: TeamTradeState,
    partner_team: TeamTradeState,
    season: str,
    trade_date: date,
    re_sign_status: ReSignStatus = ReSignStatus.UNKNOWN,
    max_assets_out: int = MAX_ASSETS_OUT,
    limit: int = DEFAULT_LIMIT,
    env: CapEnvironment | None = None,
) -> SolverResult:
    """Find legal packages satisfying ``intent``, or say why none exist."""
    started = time.monotonic()
    env = env or environment_for(season)
    result = SolverResult()

    targets = [pid for pid in dict.fromkeys(intent.target_player_ids) if pid in theirs]
    if not targets:
        result.binding_constraint = "NO_VALID_TARGET"
        result.closest_miss = (
            "none of the requested target ids are on the counterparty's roster"
        )
        result.elapsed_s = time.monotonic() - started
        return result

    incoming = sum(theirs[p].salary for p in targets)
    eligible = intent.eligible(own)
    if not eligible:
        result.binding_constraint = "NO_TRADEABLE_ASSETS"
        result.closest_miss = "every tradeable asset was excluded or unknown"
        result.elapsed_s = time.monotonic() - started
        return result

    rank = _rank(intent)
    blocking: Counter[str] = Counter()
    best_deficit: tuple[int, str] | None = None
    legal: list[Package] = []
    ceiling = max(1, min(max_assets_out, len(eligible), MAX_ASSETS_OUT))

    for size in range(1, ceiling + 1):
        for combo in combinations(eligible, size):
            outgoing = sum(own[p].salary for p in combo)

            # Cheap necessary conditions, both sides, before building a Trade.
            # This is the prune that keeps the search tractable: a full
            # validate_trade is orders of magnitude more work than two
            # arithmetic bounds, and most subsets fail one of them.
            our_limit = max_incoming_salary(outgoing, own_team.team_salary, env)
            if incoming > our_limit:
                blocking["SALARY_MATCH"] += 1
                deficit = incoming - our_limit
                if best_deficit is None or deficit < best_deficit[0]:
                    best_deficit = (
                        deficit,
                        f"sending {'+'.join(own[p].name for p in combo)} "
                        f"(${outgoing:,}) allows ${our_limit:,} back, "
                        f"${deficit:,} short of ${incoming:,}",
                    )
                continue
            their_limit = max_incoming_salary(
                incoming, partner_team.team_salary, env
            )
            if outgoing > their_limit:
                blocking["SALARY_MATCH_PARTNER"] += 1
                continue

            trade = build_trade(
                tuple(combo),
                tuple(targets),
                own=own,
                theirs=theirs,
                own_team=own_team,
                partner_team=partner_team,
                season=season,
                trade_date=trade_date,
                re_sign_status=re_sign_status,
            )
            validation = validate_trade(trade, env)
            result.examined += 1

            errors = [f for f in validation.findings if f.severity is Severity.ERROR]
            if errors:
                for finding in errors:
                    blocking[finding.rule] += 1
                continue

            outcome = validation.per_team[own_team.team_id]
            legal.append(
                Package(
                    send_player_ids=tuple(combo),
                    receive_player_ids=tuple(targets),
                    outgoing_salary=outgoing,
                    incoming_salary=incoming,
                    headroom=outcome.match.headroom,
                    verdict=validation.verdict,
                    notes=tuple(
                        str(f)
                        for f in validation.findings
                        if f.severity in (Severity.WARNING, Severity.UNDETERMINED)
                    ),
                )
            )

    # Deterministic ordering, so the same intent yields the same shortlist in
    # the same order on every run. Fewest bodies first (a GM parting with one
    # player prefers that to three), then least salary out, then the GM's own
    # stated priority, then ids to break any remaining tie.
    legal.sort(
        key=lambda p: (
            len(p.send_player_ids),
            p.outgoing_salary,
            tuple(sorted(rank.get(pid, len(rank)) for pid in p.send_player_ids)),
            p.send_player_ids,
        )
    )

    result.feasible_found = len(legal)
    result.packages = legal[:limit]
    result.truncated = len(legal) > limit
    result.elapsed_s = time.monotonic() - started

    if not legal:
        result.blocking_rules = dict(blocking.most_common())
        result.binding_constraint = (
            blocking.most_common(1)[0][0] if blocking else "NO_CANDIDATE"
        )
        result.closest_miss = best_deficit[1] if best_deficit else ""
    return result


def constraint_explanation(result: SolverResult) -> str:
    """What to hand back to an agent whose intent was unsatisfiable.

    Names the binding rule and the closest miss in dollars, because "no legal
    package" is not actionable and "you were $2.1M short" is. This is the
    feedback that the old repair retry never had: the previous loop handed back
    a rejection of a package the model had guessed at, and asked it to guess
    again. This hands back the shape of the constraint itself.
    """
    if result.satisfiable:
        return ""
    lines = [f"No legal package exists for that intent ({result.binding_constraint})."]
    if result.closest_miss:
        lines.append(f"Closest attempt: {result.closest_miss}.")
    if result.blocking_rules:
        summary = ", ".join(f"{k} x{v}" for k, v in list(result.blocking_rules.items())[:4])
        lines.append(f"Blocked by: {summary}.")
    lines.append(
        "Revise the intent: name a cheaper target, widen the tradeable assets, "
        "or drop an exclusion."
    )
    return "\n".join(lines)
