"""Ranking options without claiming more than the model supports.

``WinDelta`` intervals are about 8.5 wins wide, because that is the win model's
residual spread on held-out seasons. Two options whose projections differ by
three wins are not distinguishable by this model, and an agent that says "the
first is better" has stated something its evidence does not contain.

That is a boundary violation of the same kind the trade solver exists to
prevent. M1.5 stopped a model from asserting a trade was *legal* when only
``rules/`` can decide that. This stops a model from asserting a trade is
*better* when only the win model can decide that, and the win model usually
cannot.

So ranking goes through here or it does not happen:

  * options are grouped into **tiers** of mutually indistinguishable choices;
  * the rendering an agent sees presents a tie as a tie, with no ordering
    inside it that could be mistaken for a preference;
  * ``test_no_agent_path_ranks_by_raw_point_estimate`` asserts nothing under
    ``agents/`` sorts options by projected wins directly.

The threshold is deliberately a *decision* rather than a default buried in a
call site. ``z=1.0`` is one standard deviation of the difference, which is
already generous — at 8.5 wins residual it needs roughly a 12-win gap to
separate two options. Nothing about that is arbitrary except the choice of z,
and z is named, stated, and recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Standard deviations of the difference required to call two options apart.
#: One is lenient. Raising it makes the model quieter and more honest; lowering
#: it makes it confident about things it cannot see.
DEFAULT_Z = 1.0


@dataclass(frozen=True, slots=True)
class Option:
    """One choice, with the projection that would follow from taking it."""

    label: str
    projected_wins: float
    #: The model's residual spread. Same for every option from one model, and
    #: carried per option so a comparison across models is impossible to
    #: assemble by accident.
    residual_sd: float
    #: Anything the caller needs to map back — a package index, a player id.
    ref: object = None


@dataclass
class Comparison:
    """Options grouped into tiers of statistically indistinguishable choices."""

    options: list[Option] = field(default_factory=list)
    z: float = DEFAULT_Z

    @property
    def threshold(self) -> float:
        """Win gap required to separate two options.

        The difference of two projections from the same model. Their errors are
        correlated, so ``sqrt(2)`` is the independent-errors case and therefore
        an upper bound on the spread — which makes this threshold conservative
        in the direction of *refusing* to distinguish. That is the right
        direction to be conservative in.
        """
        if not self.options:
            return 0.0
        sd = max(o.residual_sd for o in self.options)
        return self.z * sd * float(np.sqrt(2))

    def separated(self, a: Option, b: Option) -> bool:
        return abs(a.projected_wins - b.projected_wins) > self.threshold

    def tiers(self) -> list[list[Option]]:
        """Options in descending projection, split where a real gap appears.

        Single-link grouping down the sorted list: a new tier starts only where
        consecutive options are separated. That deliberately allows a long
        chain of overlapping options to sit in one tier even when its ends are
        far apart — the honest reading, since no adjacent pair is
        distinguishable and transitivity is not available.
        """
        if not self.options:
            return []
        ordered = sorted(self.options, key=lambda o: (-o.projected_wins, o.label))
        tiers: list[list[Option]] = [[ordered[0]]]
        for previous, option in zip(ordered, ordered[1:]):
            if self.separated(previous, option):
                tiers.append([option])
            else:
                tiers[-1].append(option)
        return tiers

    @property
    def all_within_noise(self) -> bool:
        return len(self.tiers()) <= 1

    def best_tier(self) -> list[Option]:
        return self.tiers()[0] if self.options else []

    def render(self) -> str:
        """What an agent is allowed to see. Ties are shown as ties.

        Options inside a tier are listed alphabetically rather than by
        projection, so there is no residual ordering for a model to read a
        preference out of. Projected wins are deliberately absent: a number
        invites arithmetic, and the whole point is that the differences are not
        real.
        """
        tiers = self.tiers()
        if not tiers:
            return "no options"
        if len(tiers) == 1:
            names = ", ".join(sorted(o.label for o in tiers[0]))
            return (
                f"All {len(tiers[0])} options are within the model's margin of "
                f"error ({self.threshold:.1f} wins). The projection cannot "
                f"rank them: {names}. Choose on basketball grounds."
            )
        lines = [
            f"Grouped by what the projection can actually distinguish "
            f"(a gap of {self.threshold:.1f} wins is needed):"
        ]
        for i, tier in enumerate(tiers, 1):
            names = ", ".join(sorted(o.label for o in tier))
            suffix = " — indistinguishable from each other" if len(tier) > 1 else ""
            lines.append(f"  tier {i}: {names}{suffix}")
        lines.append(
            "Any option within a tier is as good as any other, as far as this "
            "model can tell."
        )
        return "\n".join(lines)


def compare_options(
    labelled: list[tuple[str, float]] | list[Option],
    residual_sd: float | None = None,
    *,
    z: float = DEFAULT_Z,
) -> Comparison:
    """Build a comparison from labels and projections, or from Options."""
    if labelled and isinstance(labelled[0], Option):
        return Comparison(options=list(labelled), z=z)  # type: ignore[arg-type]
    if residual_sd is None:
        raise ValueError(
            "residual_sd is required: a comparison without the model's error "
            "is a ranking that claims certainty it does not have"
        )
    return Comparison(
        options=[Option(label, wins, residual_sd) for label, wins in labelled],
        z=z,
    )
