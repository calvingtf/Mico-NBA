"""A stipulated SIGNING: the second kind of seed event.

    "LeBron James signs with the Golden State Warriors"

A stipulated trade goes through ``rules/trade_validator.py``. A signing has
no counterparty and no salary matching, so that validator has nothing to say
about it; the question is instead whether the destination has a **legal
route** to sign this player on the seed date, which is what
``rules/signing_solver.feasible_signings`` and ``rules/signing.signing_routes``
answer. The boundary is identical to the trade path: the model names a player
and a team, and the deterministic layer decides whether that is possible and
on what terms.

**The amount is derived, never stated.** No schema field carries a salary,
so a sentence cannot smuggle one in. The runner takes the best legal route's
first-year maximum unless the scenario file declares a route by name, and
records which of those two happened - a derived figure that presented itself
as a stated one would be the same failure as a model quoting a salary.

**A signing is refused the same way a trade is.** If no route exists, the
run stops and quotes the blocking reason per route from ``rules/``. That is
a real answer about the counterfactual, not a failure of the flow.
"""

from __future__ import annotations

from dataclasses import dataclass

from mironba.rules.signing import FreeAgent, SigningResult, signing_routes
from mironba.rules.signing_solver import feasible_signings


@dataclass(frozen=True)
class StipulatedSigning:
    """The asserted signing, with every figure derived from the snapshot."""

    player_id: str
    name: str
    to_team: str
    label: str
    #: Route the scenario file declared, or "" to take the solver's best.
    declared_route: str = ""


def signable_pool(league, league_mod) -> set[str]:
    """Exactly the pool the reaction itself signs from.

    NOT ``free_agent_pool()``. That is the narrow set - a 2025-26 contract
    and no 2026-27 deal anywhere - and it excludes every player who arrived
    somewhere AFTER the freeze, because he does have a 2026-27 row. Those
    post-freeze arrivals are precisely the interesting names: run_branch adds
    them back (``base_pool | all_added``) because at the freeze date they had
    not yet signed anywhere, and they are the players the league contests.

    Checking a stipulation against the narrow pool would refuse every
    signing worth stipulating, and would refuse it with the wrong reason.
    """
    from mironba.sim.arrivals import pre_freeze_ids

    already = pre_freeze_ids(league_mod.ARRIVALS)
    added = set().union(*(league.arrivals(t) - already
                          for t in league_mod.TEAMS))
    return league.free_agent_pool() | added


def build_signing(scenario, league, league_mod=None) -> StipulatedSigning:
    """Read the declared signing. Refuses a player nobody could sign."""
    spec = dict(scenario.stipulation.get("signing") or {})
    pid = spec.get("player_id", "")
    to_team = spec.get("to", "")
    if not pid or not to_team:
        raise SystemExit(
            "a signing stipulation needs player_id and to; "
            f"{scenario.id} declares {spec!r}"
        )
    if league_mod is not None:
        # A signing scenario must NOT name its signee as decision_subject.
        # run_branch removes SUBJECT from the signable pool - correct for a
        # pending decision, where the subject is committed separately - and
        # it removes him from the UNSEEDED run too. The null then cannot
        # contest him, "who else wanted him" comes back empty, and the diff
        # reports that the seed changed nothing. Every one of those readings
        # is false, and none of them looks like an error. Caught by writing
        # the first signing scenario this way.
        if getattr(league_mod, "SUBJECT", None) == pid:
            raise SystemExit(
                f"{pid} is this scenario's decision_subject AND its "
                "stipulated signee. run_branch excludes SUBJECT from the "
                "signable pool, so the run WITHOUT the seed could not "
                "contest him either - the comparison the run exists to make "
                "would silently return nothing. Drop decision_subject from "
                "the scenario file, or stipulate a different player."
            )
        pool = signable_pool(league, league_mod)
        if pid not in pool:
            where = {r["player_id"]: r["team_id"]
                     for r in league.contracts_2627}.get(pid, "nowhere")
            raise SystemExit(
                f"{pid} is not signable at the freeze: he is under contract "
                f"with {where} on {scenario.freeze} and was there before it. "
                "A signing cannot be stipulated for a player already under "
                "contract - that event is a TRADE, and the trade path is "
                "where it belongs. The snapshot decides this, not the "
                "sentence."
            )
    return StipulatedSigning(
        player_id=pid, name=league.name(pid), to_team=to_team,
        label=scenario.stipulation.get("label", scenario.id),
        declared_route=str(spec.get("route", "")),
    )


def agent_for(league, pid: str, team: str) -> FreeAgent:
    """The free agent as the signing rules see him. Mirrors sim/league.py."""
    from mironba.sim.league import MINIMUM_CAP_HIT_TIER, SERVICE_YEARS

    return FreeAgent(
        player_id=pid, name=league.name(pid),
        years_of_service=SERVICE_YEARS.get(pid, MINIMUM_CAP_HIT_TIER),
        prior_salary=league.prior_salary.get(pid, 0),
        years_with_team=league.rights(pid, team),
    )


def routes_for(signing: StipulatedSigning, league, env) -> SigningResult:
    """Every legal way the destination could sign him, on the seed date.

    ``feasible_signings`` is asked first even for one player: it owns the
    roster-full answer, and "the roster is full, so salary is not the
    binding constraint" is a materially better refusal than an empty route
    list.
    """
    state = league.freeze_state(signing.to_team, set())
    agent = agent_for(league, signing.player_id, signing.to_team)
    scan = feasible_signings(state, [agent], env)
    result = signing_routes(state, agent, env)
    if not scan.signings and not result.routes and not result.blocked:
        result.blocked["roster"] = scan.empty_reason or "no route and no reason"
    return result


def chosen_route(signing: StipulatedSigning, result: SigningResult):
    """The route the run uses, and how it was arrived at.

    Returns ``(route, source)``. ``source`` is the honest half: a declared
    route is the scenario file's decision, an undeclared one is the
    solver's, and the manifest records which.
    """
    if signing.declared_route:
        for route in result.routes:
            if route.route == signing.declared_route:
                return route, "declared in the scenario file"
        return None, (
            f"the scenario declares route {signing.declared_route!r}, which "
            f"is not among the legal routes "
            f"({', '.join(result.route_names()) or 'none'})"
        )
    best = result.best()
    if best is None:
        return None, "no legal route exists"
    return best, ("solver's best route; the sentence stated no amount and no "
                  "route, so this is derived rather than declared")


def apply_signing(league, signing: StipulatedSigning, salary: int) -> None:
    """Put the signed contract into the loaded world state.

    A signing ADDS a row where a trade moves one, so the stipulated player
    exists in ``contracts_2627`` only after this runs. Everything downstream
    that reads a roster or a payroll therefore sees him on his new team,
    which is what makes the reaction's cap arithmetic correct.
    """
    existing = [r for r in league.contracts_2627
                if r["player_id"] == signing.player_id]
    if existing:
        # A post-freeze arrival already has a row: he signed SOMEWHERE after
        # the freeze in the real world. The stipulation overrides where and
        # for how much; appending a second row would leave him on two
        # rosters and double-count his salary league-wide.
        for row in existing:
            row["team_id"] = signing.to_team
            row["salary"] = str(int(salary))
    else:
        template = league.contracts_2627[0]
        row = {key: "" for key in template}
        row.update({
            "player_id": signing.player_id,
            "team_id": signing.to_team,
            "salary": str(int(salary)),
            "season": template.get("season", ""),
            "final_season": template.get("season", ""),
            "fully_guaranteed": "1",
            "option": "",
        })
        league.contracts_2627.append(row)
    # Assert him onto the freeze books; see LeagueState.covered_at_freeze.
    if not hasattr(league, "_stipulated_signings"):
        league._stipulated_signings = set()
    league._stipulated_signings.add(signing.player_id)


def pursuers_of(player_id: str, contests) -> list[dict]:
    """Who else wanted him, from a run where he was NOT stipulated.

    The seeded run excludes a stipulated signee from the pool entirely - he
    must not sign anywhere else - so it has no contest for him to read. The
    UNSEEDED run does, and its offer list is the honest answer to "who else
    pursued him": teams that made a legal offer under an enumerated route,
    not teams a model thought were interested.
    """
    for contest in contests:
        if contest.player_id != player_id:
            continue
        return [
            {"team": offer.team, "route": offer.route,
             "amount": offer.max_first_year}
            for offer in sorted(contest.offers, key=lambda o: -o.max_first_year)
        ]
    return []
