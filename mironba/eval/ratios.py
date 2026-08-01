"""A ratio takes its filter once, and applies it to both sides.

Three published figures in this project were scope mismatches with the same
shape: a numerator counted over one population and a denominator over another.

* **Recall of 200%** — numerator counted matching *proposals*, denominator
  counted *real trades*. Several proposals hit one trade.
* **The pooled precision null of 6.67%** — qualifying pairs unioned *across*
  seasons, divided by one season's pair space. Corrected to 2.58%, which
  flipped the sign of the headline.
* **Recall of 165%** — numerator counted trades the scorer credits, denominator
  counted trades the representability rule admits. The two disagreed about
  three-team deals.

None raised. Each produced a number that looked plausible until someone did the
arithmetic in their head. The common cause is that a ratio is built from two
independently-derived quantities, and nothing forces them to describe the same
population.

:class:`Ratio` takes the population and the filter **once**, so numerator and
denominator cannot diverge. Passing two pre-filtered collections is refused.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class ScopeMismatch(ValueError):
    """A ratio was constructed from two differently-scoped sets."""


@dataclass(frozen=True, slots=True)
class Ratio:
    """``numerator / denominator``, both derived from one filtered population.

    ``population`` is filtered by ``scope`` once. ``hit`` then selects the
    numerator from the survivors. There is no way to hand in a numerator drawn
    from somewhere else, which is the entire point.
    """

    label: str
    population: tuple[Any, ...]
    scope: Callable[[Any], bool] | None = None
    hit: Callable[[Any], bool] = bool

    @property
    def in_scope(self) -> tuple[Any, ...]:
        if self.scope is None:
            return self.population
        return tuple(item for item in self.population if self.scope(item))

    @property
    def denominator(self) -> int:
        return len(self.in_scope)

    @property
    def numerator(self) -> int:
        return sum(1 for item in self.in_scope if self.hit(item))

    @property
    def value(self) -> float:
        return self.numerator / self.denominator if self.denominator else 0.0

    def __post_init__(self) -> None:
        if self.numerator > self.denominator:
            raise ScopeMismatch(
                f"{self.label}: numerator {self.numerator} exceeds denominator "
                f"{self.denominator}. A ratio above 1 means the two sides "
                "describe different populations - this is the shape of the "
                "200% recall, the unioned pooled null, and the 165% recall."
            )

    def render(self, null: float | None = None) -> str:
        text = f"  {self.label:<34} {self.numerator:>4}/{self.denominator:<4} = {self.value:>6.1%}"
        if null is not None:
            headroom = (self.value - null) / (1 - null) if null < 1 else 0.0
            ratio = self.value / null if null else float("inf")
            text += f"   null {null:>6.1%}   {ratio:>4.2f}x   headroom {headroom:>+6.2%}"
        return text


def ratio_of(
    label: str,
    population: Iterable[Any],
    *,
    scope: Callable[[Any], bool] | None = None,
    hit: Callable[[Any], bool] = bool,
) -> Ratio:
    return Ratio(label=label, population=tuple(population), scope=scope, hit=hit)


def refuse_prefiltered(numerator_set, denominator_set) -> None:
    """Guard for call sites that still hold two collections.

    There is no safe way to combine them: equal sizes prove nothing, and a
    smaller numerator set may still be drawn from a different population.
    """
    raise ScopeMismatch(
        "build a Ratio from one population and a filter. Two pre-filtered "
        "collections cannot be checked for scope agreement - a numerator that "
        f"happens to be smaller ({len(numerator_set)} vs "
        f"{len(denominator_set)}) is not evidence they describe the same set."
    )
