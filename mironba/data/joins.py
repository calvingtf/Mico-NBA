"""Keyed lookups that report their own hit rate.

Instance #13 was a dict lookup whose default fired on **100%** of keys and
produced a perfectly plausible number: the "degree-preserving" null was keyed by
team abbreviation against a universe of integers, so every weight became 1 and
the null was the uniform one wearing a label. Nothing at runtime said so.

It is the same family as ``allowed_tools=[]`` filtering nothing, and as the
salary-blindness claim: a mechanism that appears to be working because its
failure mode is indistinguishable from its success mode at the call site.

A lookup with a fallback cannot say whether it matched. This makes it say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class JoinTooLossy(RuntimeError):
    """A keyed lookup missed more often than its declared tolerance."""


@dataclass
class Join:
    """A lookup that records matched/total and refuses to be silently empty.

    ``max_miss_rate`` is a *declaration*, not a tuning knob: it says how lossy
    this particular join is expected to be, so an unexpected change is loud.
    A join that legitimately misses 40% declares 0.45, and then a regression to
    90% still raises.
    """

    name: str
    table: dict
    max_miss_rate: float = 0.5
    default: object = None
    matched: int = 0
    total: int = 0
    missed_keys: list = field(default_factory=list)

    def get(self, key):
        self.total += 1
        if key in self.table:
            self.matched += 1
            return self.table[key]
        if len(self.missed_keys) < 20:
            self.missed_keys.append(key)
        return self.default

    @property
    def hit_rate(self) -> float:
        return self.matched / self.total if self.total else 0.0

    @property
    def miss_rate(self) -> float:
        return 1 - self.hit_rate

    def check(self) -> None:
        """Raise if the join missed more than declared. Call after use."""
        if not self.total:
            return
        if self.miss_rate > self.max_miss_rate:
            raise JoinTooLossy(
                f"join {self.name!r} matched {self.matched}/{self.total} "
                f"({self.hit_rate:.1%}); miss rate {self.miss_rate:.1%} exceeds "
                f"the declared {self.max_miss_rate:.0%}. Sample misses: "
                f"{self.missed_keys[:5]}. A default firing this often produces "
                "a plausible number that means nothing."
            )

    def report(self) -> str:
        return (
            f"  {self.name:<28} {self.matched:>5}/{self.total:<5} "
            f"= {self.hit_rate:>5.1%} hit"
            + ("" if self.miss_rate <= self.max_miss_rate else "   ** OVER TOLERANCE **")
        )
