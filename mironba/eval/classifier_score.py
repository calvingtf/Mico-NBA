"""Accuracy is never reported alone. The predicted distribution goes with it.

A degenerate predictor and a mediocre one score identically against a
balanced null, and they require opposite responses. Entry #74 is the clean
case: a trade-vs-signing field scored 6 of 12 against a 6-of-12 null, which
reads as "no better than chance, needs a better prompt". The distribution
says something else entirely - it emitted "trade" twelve times out of twelve
and "signing" never. It was not a weak classifier. It was a constant, and no
amount of prompting improves a constant; the fix was structural (ask the
question alone) and gave 12 of 12.

**The family this belongs to.** Three instances now, all the same shape - a
mechanism whose failure and success look alike at the call site:

* the degree-preserving null with degenerate variance, where a ratio was
  dividing by something with no spread (entry #29);
* a weighting that silently collapsed to uniform, so a "weighted" result and
  an unweighted one were the same number;
* this: a classifier that never emits one of its classes.

In each, the headline number was computable, plausible, and uninformative,
and the only thing that separated working from broken was a second statistic
nobody had asked for. So this module makes asking structural: ``score()``
returns the distribution alongside the accuracy, and ``report()`` renders
both or neither.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClassifierScore:
    """One classifier's accuracy AND what it actually predicted."""

    n: int
    correct: int
    #: predicted label -> times emitted. The statistic the accuracy hides.
    predicted: dict = field(default_factory=dict)
    #: true label -> times present. Fixes what the null is.
    truth: dict = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else 0.0

    @property
    def majority_null(self) -> float:
        """What always answering the most common true label scores."""
        return max(self.truth.values()) / self.n if self.n else 0.0

    @property
    def classes_never_predicted(self) -> list[str]:
        """True labels the predictor never once emitted."""
        return sorted(set(self.truth) - {k for k, v in self.predicted.items()
                                         if v})

    @property
    def degenerate(self) -> bool:
        """True when the predictor emits fewer classes than exist.

        A degenerate predictor is not a weak one. It carries no information
        about the classes it never names, whatever its accuracy, and the
        response is structural rather than a better prompt.
        """
        return bool(self.classes_never_predicted)

    @property
    def beats_null(self) -> bool:
        return self.accuracy > self.majority_null

    def exact_binomial_p(self) -> float:
        """P(at least this many correct) under the majority-class rate.

        Exact, no scipy: the sets this is used on are small enough that the
        closed form is cheap and a normal approximation would be reaching.
        """
        from math import comb

        p = self.majority_null
        if self.n == 0 or p <= 0 or p >= 1:
            return 1.0
        return sum(comb(self.n, k) * p**k * (1 - p) ** (self.n - k)
                   for k in range(self.correct, self.n + 1))


def score(pairs) -> ClassifierScore:
    """``pairs`` is an iterable of (truth, predicted)."""
    rows = list(pairs)
    truth = Counter(t for t, _ in rows)
    predicted = Counter(p for _, p in rows)
    return ClassifierScore(
        n=len(rows),
        correct=sum(1 for t, p in rows if t == p),
        predicted=dict(predicted),
        truth=dict(truth),
    )


def report(result: ClassifierScore, label: str = "") -> str:
    """Accuracy and distribution, in one string, or nothing.

    Deliberately not two functions. A caller that can print the accuracy
    without the distribution eventually will, and then a constant predictor
    ships looking like a weak one.
    """
    head = f"{label + ': ' if label else ''}"
    lines = [
        f"{head}{result.correct}/{result.n} = {result.accuracy:.1%} "
        f"(majority-class null {result.majority_null:.1%})",
        "  predicted: " + ", ".join(
            f"{k} x{v}" for k, v in sorted(result.predicted.items())),
        "  truth:     " + ", ".join(
            f"{k} x{v}" for k, v in sorted(result.truth.items())),
    ]
    if result.degenerate:
        lines.append(
            "  DEGENERATE: never predicted "
            + ", ".join(result.classes_never_predicted)
            + " - this is a constant, not a weak classifier; the accuracy "
              "above is uninformative about those classes and prompting "
              "will not move it")
    elif not result.beats_null:
        lines.append("  at or below its null, and NOT degenerate - it does "
                     "use both classes, so this is a genuinely weak "
                     "classifier and a different problem from #74")
    else:
        lines.append(f"  P(>= this many correct | null) = "
                     f"{result.exact_binomial_p():.5f}")
    return "\n".join(lines)
