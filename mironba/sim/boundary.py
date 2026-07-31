"""The LLM -> rules boundary: judgement, and the BYC question.

The charter's first non-negotiable is that an LLM may propose a trade and only
``rules/`` may approve one. Under M1.5 that is enforced further upstream than
this module: the LLM emits a *TradeIntent*, never a package, and
``rules/solver.py`` constructs every package that exists. There is no longer an
untrusted structure to assemble, which is why this file is mostly gone.

What remains is the judgement call itself and the base-year-compensation
question, which is a property of the scenario rather than of any package.

``judge`` is a one-line wrapper on ``validate_trade`` on purpose: it gives the
crossing point a name that shows up in call graphs, so
``test_no_agent_or_llm_module_imports_the_validator`` has something specific to
allow and everything else to forbid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from mironba.rules.trade_validator import (
    ReSignStatus,
    Trade,
    TradeValidation,
    Verdict,
    validate_trade,
)


@dataclass(frozen=True, slots=True)
class BYCResolution:
    """How base-year compensation was resolved for this scenario.

    The snapshot cannot answer it — Basketball-Reference does not publish
    re-sign status, so every player loads as UNKNOWN and every trade comes back
    UNDETERMINED. A scenario may assert an answer, but the assertion is a human
    input like any other and is recorded as one: ``sourced`` is False unless
    somebody actually checked, and the manifest carries the flag either way.
    """

    mode: str = "unresolved"  # unresolved | assume_not_re_signed
    sourced: bool = False
    note: str = ""

    @property
    def status(self) -> ReSignStatus:
        if self.mode == "assume_not_re_signed":
            return ReSignStatus.NOT_RE_SIGNED
        return ReSignStatus.UNKNOWN

    @property
    def is_assumption(self) -> bool:
        return self.mode != "unresolved" and not self.sourced


def judge(trade: Trade) -> TradeValidation:
    """The only approval path in the codebase.

    A one-line wrapper on purpose: it gives the boundary a name that shows up
    in call graphs and in ``test_only_rules_may_approve``, so an agent that
    ever starts deciding legality for itself is visible rather than subtle.
    """
    return validate_trade(trade)


def rejection_reason(validation: TradeValidation) -> str:
    """What to hand back to the agent for its one retry.

    Errors only, and the rule ids with them. The agent gets the CBA's actual
    objection rather than a paraphrase, which is the difference between a
    revision and another guess.
    """
    if validation.verdict is Verdict.UNDETERMINED:
        return "\n".join(
            f"- UNDETERMINED {f.rule}: {f.message}" for f in validation.undetermined()
        )
    return "\n".join(f"- {f.rule}: {f.message}" for f in validation.errors())
