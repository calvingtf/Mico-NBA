"""Deterministic CBA rules. No LLM output ever enters this package.

An LLM may *propose* a trade; only this package may *approve* one.
"""

from mironba.rules.cap import ApronTier, TeamSalaryState, max_incoming_salary, tier_for_salary
from mironba.rules.constants import CONTESTED, CapEnvironment, ContestedRule, environment_for
from mironba.rules.trade_validator import (
    CashAsset,
    Finding,
    PickAsset,
    PlayerAsset,
    ReSignStatus,
    Severity,
    TeamTradeState,
    Trade,
    TradeException,
    TradeValidation,
    Verdict,
    VerdictUndetermined,
    validate_trade,
)

__all__ = [
    "CONTESTED",
    "ApronTier",
    "CapEnvironment",
    "CashAsset",
    "ContestedRule",
    "Finding",
    "PickAsset",
    "PlayerAsset",
    "ReSignStatus",
    "Severity",
    "TeamSalaryState",
    "TeamTradeState",
    "Trade",
    "TradeException",
    "TradeValidation",
    "Verdict",
    "VerdictUndetermined",
    "environment_for",
    "max_incoming_salary",
    "tier_for_salary",
    "validate_trade",
]
