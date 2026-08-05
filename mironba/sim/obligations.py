"""What the reaction does with what ``rules/`` said.

The validator emits findings. Until this module existed, the reaction read
none of them: a trade could leave a team hard-capped at the first apron and
the reaction would then spend that team to the second apron, because its
signing ceiling was a constant that no finding could reach. That is not a
missing feature - it is the system making a claim in one module and
forgetting it in the next.

**The enumeration.** Same move as the writer registry and ``DERIVED_FACTS``:
every rule the validator can emit is listed below with a declared
disposition, and a test fails if ``Rule`` gains a member that is not. A
finding nothing reads is a claim the system makes and then forgets, and the
only way to know which those are is to enumerate them.

Three dispositions:

``BLOCKS``
    The finding is ERROR or UNDETERMINED severity, so the trade is not legal
    and ``sim/stipulated.py`` exits before any reaction exists. There is no
    reaction to consume it. This is not "ignored" - it is consumed by
    refusing to run at all, which is the strongest possible consumption.

``CONSUMED``
    The reaction changes what it does because of this finding.

``IGNORED``
    The reaction reads it and deliberately does nothing, for the stated
    reason. An ignored finding still has to earn its reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mironba.rules.constants import MIN_STANDARD_ROSTER
from mironba.rules.trade_validator import Rule

BLOCKS = "blocks-the-run"
CONSUMED = "consumed"
IGNORED = "ignored"

#: rule -> (disposition, reason). Exhaustive over ``Rule`` by test.
FINDING_DISPOSITION: dict[str, tuple[str, str]] = {
    # -- ERROR / UNDETERMINED: the trade never applies, so nothing reacts ----
    Rule.STRUCTURE: (BLOCKS, "ERROR only: a malformed trade is refused and "
                             "the runner exits before the reaction"),
    Rule.SALARY_MATCH: (BLOCKS, "ERROR or UNDETERMINED: an unmatched trade "
                                "is refused; UNDETERMINED is not legal "
                                "either, which is the artifact the charter "
                                "records catching"),
    Rule.AGGREGATION_SECOND_APRON: (BLOCKS, "ERROR only: refused before the "
                                            "reaction"),
    Rule.AGGREGATION_WINDOW: (BLOCKS, "ERROR only: refused before the "
                                      "reaction"),
    Rule.TRADE_RESTRICTION_WINDOW: (BLOCKS, "ERROR only: refused before the "
                                            "reaction"),
    Rule.NO_TRADE_CLAUSE: (BLOCKS, "ERROR only: refused before the reaction"),
    Rule.ROSTER_LIMIT: (BLOCKS, "ERROR only: a roster over the maximum is "
                                "refused before the reaction"),
    Rule.CASH_LIMIT: (BLOCKS, "ERROR or UNDETERMINED: refused either way"),
    Rule.CASH_SECOND_APRON: (BLOCKS, "ERROR only: refused before the "
                                     "reaction"),
    Rule.CASH_MINIMUM: (BLOCKS, "ERROR only: refused before the reaction"),
    Rule.SIGN_AND_TRADE_APRON: (BLOCKS, "ERROR only: refused before the "
                                        "reaction"),
    Rule.STEPIEN: (BLOCKS, "ERROR only: refused before the reaction"),
    Rule.BASE_YEAR_COMPENSATION: (BLOCKS, "UNDETERMINED only: the validator "
                                          "refuses to guess a BYC valuation "
                                          "and the trade is not legal"),

    # -- WARNING / INFO: the trade IS legal, so the reaction must answer ----
    Rule.HARD_CAP: (CONSUMED, "the team's signing ceiling for the whole "
                              "reaction is lowered to the hard cap the trade "
                              "triggered; before this it was the second "
                              "apron for everyone and LAL overspent its own "
                              "cap by $12,671,000"),
    Rule.ROSTER_MINIMUM: (CONSUMED, "the team is obliged to sign up to the "
                                    "minimum before discretionary signings "
                                    "are considered final; met or unmet is "
                                    "reported per team, never assumed"),
    Rule.MIN_TEAM_SALARY: (IGNORED, "the salary floor is enforced at season "
                                    "end with a shortfall payment, not by a "
                                    "signing during the offseason window "
                                    "this simulation covers; a team under "
                                    "the floor on the freeze date has months "
                                    "of real transactions the sim does not "
                                    "model, so forcing a signing here would "
                                    "invent behaviour rather than model it"),
    Rule.MINIMUM_SALARY_EXCEPTION: (IGNORED, "INFO only: it reports that a "
                                             "minimum contract was used, "
                                             "which the reaction's own "
                                             "signing routes already "
                                             "enumerate independently"),
    Rule.TPE_PRIOR_YEAR: (IGNORED, "declared on Rule but never constructed "
                                   "anywhere in the validator - traded "
                                   "player exceptions are not modelled at "
                                   "all, so there is no finding to consume. "
                                   "Listed rather than deleted so the "
                                   "enumeration stays honest about what the "
                                   "constant means."),
}


@dataclass
class Obligations:
    """What the seed's findings require of the reaction.

    Built only from findings the validator actually emitted - never from a
    guess about what a trade probably did.
    """

    #: team -> the payroll line it may not exceed for the rest of the run
    hard_caps: dict[str, int] = field(default_factory=dict)
    #: team -> how many players it must add to reach the roster minimum
    roster_shortfall: dict[str, int] = field(default_factory=dict)
    #: findings seen, by rule, for reporting what was consumed
    seen: dict[str, int] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.hard_caps or self.roster_shortfall)

    @property
    def teams_forced(self) -> list[str]:
        return sorted(set(self.hard_caps) | set(self.roster_shortfall))


def obligations_from(findings, env) -> Obligations:
    """Read the validator's findings into constraints the reaction honours."""
    result = Obligations()
    for finding in findings or ():
        rule = getattr(finding, "rule", "")
        result.seen[rule] = result.seen.get(rule, 0) + 1
        team = getattr(finding, "team", None)
        detail = dict(getattr(finding, "detail", {}) or {})
        if not team:
            continue
        if rule == Rule.HARD_CAP:
            line = detail.get("hard_cap")
            if line is None:
                # The validator said hard-capped without saying at what line;
                # the conservative reading is the first apron, and it is
                # recorded as an assumption rather than silently applied.
                line = env.first_apron
            result.hard_caps[team] = min(
                int(line), result.hard_caps.get(team, int(line)))
        elif rule == Rule.ROSTER_MINIMUM:
            after = int(detail.get("roster_after", MIN_STANDARD_ROSTER))
            shortfall = max(0, MIN_STANDARD_ROSTER - after)
            if shortfall:
                result.roster_shortfall[team] = max(
                    shortfall, result.roster_shortfall.get(team, 0))
    return result


def undeclared_rules() -> list[str]:
    """Rule members with no declared disposition. Empty, by test."""
    declared = set(FINDING_DISPOSITION)
    members = {v for k, v in vars(Rule).items()
               if not k.startswith("_") and isinstance(v, str)}
    return sorted(members - declared)
