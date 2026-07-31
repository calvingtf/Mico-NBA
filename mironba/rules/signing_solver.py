"""Which free agents a team can actually sign. The signing solver.

Same architecture as ``rules/solver.py``, for the same reason. The model names
a player and a route; the deterministic layer produces the terms. Nothing
upstream states an amount, so an over-cap signing is unrepresentable rather
than merely discouraged — exactly as an illegal package is.

The trade solver's shape carried over:

  ``signing_routes``      one player -> every legal route, or why there is none
  ``feasible_signings``   one team   -> every reachable free agent, no prices
  ``absorbable_ceiling``  the cheap O(1) bound the scan prunes on

The admissibility rule from M1.6 applies unchanged and is the thing most worth
testing: **a bound used to prune must never exclude a signing brute force would
find.** The trade solver's prune was unsound for months and silently deleted
twelve legal Lakers packages; the same mistake here would report that a team
cannot sign someone it can.

What makes the bound sound is the minimum exception. It is available to every
team at every cap position, so the ceiling is never below the minimum for the
player's service level — which is why Philadelphia could sign LeBron James
while carrying $58.1M, $57.1M and $40.8M at the top of its roster.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from mironba.rules.constants import MAX_STANDARD_ROSTER
from mironba.rules.signing import (
    MINIMUM,
    ROUTES,
    FreeAgent,
    SigningResult,
    TeamCapState,
    signing_routes,
)


@dataclass(frozen=True, slots=True)
class FeasibleSigning:
    """A free agent this team could actually sign, with no price attached.

    The signing analogue of ``FeasibleTarget``, and it carries the same
    guarantee: names and route labels, never an amount.
    ``test_feasible_signings_carry_no_money`` enforces it field by field and
    again on the rendered text.

    A route *name* is not a price. "You could get him with the mid-level" tells
    a GM which lever exists; it does not tell him what the lever is worth, and
    the solver still produces the terms.
    """

    player_id: str
    name: str
    route_count: int
    #: Route labels, most valuable first. No amounts.
    routes: tuple[str, ...] = ()

    def render(self) -> str:
        label = "route" if self.route_count == 1 else "routes"
        return (
            f"  {self.player_id:<12} {self.name:<26} "
            f"{self.route_count} {label}: {', '.join(self.routes)}"
        )


@dataclass
class SigningScan:
    """Who is signable, and what it cost to work out."""

    signings: list[FeasibleSigning] = field(default_factory=list)
    considered: int = 0
    survived_bound: int = 0
    ceiling: int = 0
    prefilter_s: float = 0.0
    solve_s: float = 0.0
    empty_reason: str = ""

    @property
    def any_feasible(self) -> bool:
        return bool(self.signings)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(s.player_id for s in self.signings)

    def render(self) -> str:
        return "\n".join(s.render() for s in self.signings)

    def explain(self) -> str:
        if self.signings:
            return (
                f"{len(self.signings)} signable of {self.considered} "
                f"({self.survived_bound} passed the bound) — "
                f"filter {self.prefilter_s * 1000:.1f}ms, "
                f"solve {self.solve_s * 1000:.1f}ms"
            )
        return f"no signable free agent: {self.empty_reason}"


def absorbable_ceiling(team: TeamCapState, env) -> int:
    """Most first-year salary this team could pay *anyone*, by any route.

    Sound for pruning by construction: it takes the maximum over every route's
    headline amount without checking whether the team qualifies for that route.
    A team that does not qualify gets a bound that is too generous, which costs
    a wasted full solve; a team that does qualify can never be pruned out.

    Cap room is included because a team under the cap can pay it, and the
    largest exception is included because a team over the cap can. The maximum
    of the two dominates both.
    """
    return max(
        team.cap_room(env),
        env.non_taxpayer_mle,
        env.taxpayer_mle,
        env.room_exception,
        env.bi_annual_exception,
        # Bird rights can reach the max salary, so an incumbent is only ever
        # bounded by the league maximum. Using the 35% tier keeps the bound
        # above every service level.
        (env.salary_cap * 35) // 100,
    )


def feasible_signings(
    team: TeamCapState,
    free_agents: list[FreeAgent],
    env,
    *,
    limit: int | None = None,
) -> SigningScan:
    """Every free agent this team has a legal route to sign.

    Two passes, in cost order, mirroring ``scan_targets``:

      1. an O(1) bound per free agent, which drops nobody a full solve would
         have kept — see ``absorbable_ceiling``;
      2. a full ``signing_routes`` per survivor, so every name returned is
         backed by an enumerated route rather than by a bound.

    In practice the bound prunes almost nothing, because the minimum exception
    reaches every team. That is the correct outcome rather than a defect: the
    honest answer to "who can this team sign" is usually "anyone, at the
    minimum", and the interesting content is *which routes* and *how much*.
    """
    scan = SigningScan()

    started = time.monotonic()
    scan.considered = len(free_agents)
    ceiling = absorbable_ceiling(team, env)
    scan.ceiling = ceiling
    if team.roster_count >= MAX_STANDARD_ROSTER:
        scan.prefilter_s = time.monotonic() - started
        scan.empty_reason = (
            f"roster is full at {team.roster_count} of {MAX_STANDARD_ROSTER}; "
            "a signing needs an open standard slot, so salary is not the "
            "binding constraint here"
        )
        return scan
    # Everyone survives the bound whenever the minimum exception is live, which
    # is nearly always. Kept anyway: it is the structure the trade solver uses,
    # it is what a future roster-hold or hard-cap rule would prune on, and a
    # scan with no bound has nowhere to put one later.
    survivors = list(free_agents)
    scan.survived_bound = len(survivors)
    scan.prefilter_s = time.monotonic() - started

    started = time.monotonic()
    for agent in survivors:
        result = signing_routes(team, agent, env)
        if result.any_route:
            scan.signings.append(
                FeasibleSigning(
                    player_id=agent.player_id,
                    name=agent.name,
                    route_count=len(result.routes),
                    routes=result.route_names(),
                )
            )
    scan.solve_s = time.monotonic() - started

    if limit is not None:
        scan.signings = scan.signings[:limit]
    if not scan.signings:
        scan.empty_reason = (
            f"{scan.considered} free agent(s) considered and none has a legal "
            "route, which should be impossible while the minimum exception "
            "exists — check the roster count and the minimum-salary scale"
        )
    return scan


@dataclass(frozen=True, slots=True)
class SigningCheck:
    """Whether the solver reproduces one real, known signing."""

    player: str
    actual_salary: int
    route_found: bool
    within_maximum: bool
    matched_route: str | None
    max_first_year: int
    routes: tuple[str, ...]

    @property
    def reproduced(self) -> bool:
        return self.route_found and self.within_maximum

    def line(self) -> str:
        mark = "OK  " if self.reproduced else "GAP "
        route = self.matched_route or "-"
        return (
            f"  {mark}{self.player:<20} ${self.actual_salary:>12,}  "
            f"via {route:<18} max ${self.max_first_year:>12,}  "
            f"routes: {', '.join(self.routes) or 'none'}"
        )


def check_signing(
    team: TeamCapState,
    player: FreeAgent,
    actual_salary: int,
    env,
    *,
    expected_route: str | None = None,
) -> SigningCheck:
    """Does the solver find a route that could have paid this actual contract?

    Deliberately does not ask whether the solver picks the *same* route a team
    used — several routes can pay the same salary and the choice between them
    turns on hard-cap consequences the team may weigh differently. It asks the
    answerable question: is there a legal route whose maximum covers what was
    actually paid.
    """
    result = signing_routes(team, player, env)
    covering = [r for r in result.routes if r.max_first_year >= actual_salary]
    matched = None
    if expected_route:
        matched = next(
            (r.route for r in covering if r.route == expected_route), None
        )
    if matched is None and covering:
        # Cheapest covering route: the one a team would use if it wanted to
        # preserve its larger exceptions, which is usually what happened.
        matched = min(
            covering, key=lambda r: (r.max_first_year, ROUTES.index(r.route))
        ).route
    return SigningCheck(
        player=player.name,
        actual_salary=actual_salary,
        route_found=bool(result.routes),
        within_maximum=bool(covering),
        matched_route=matched,
        max_first_year=result.max_first_year,
        routes=result.route_names(),
    )
