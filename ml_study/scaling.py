"""Online feature scaling for heterogeneous, varying data ranges.

The observation space mixes wide physical envelopes (speed, demand) with already
normalised states (SoC). A learner converges far faster when every input is on a
comparable scale, so this module offers two streaming normalisers:

* :class:`MinMaxScaler` - deterministic, range-based scaling to ``[0, 1]`` using
  the known operating ranges from :mod:`ml_study.features`. Stable from sample
  one; the natural default for a reproducible study.
* :class:`OnlineStandardizer` - adaptive zero-mean / unit-variance scaling using
  Welford's algorithm, updated **one sample at a time** (the project's
  "increment by 1" learning rule). Use it when the true ranges are unknown and
  must be discovered from the data stream itself.

Both are pure standard library and allocation-light so they can run inside the
real-time control loop a Unified AI would eventually occupy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .features import FEATURE_RANGES


class RunningStats:
    """Welford online mean/variance for a single scalar stream.

    ``update`` advances the sample count by exactly one - the incremental
    learning primitive the rest of the study is built on.
    """

    __slots__ = ("n", "mean", "_m2")

    def __init__(self) -> None:
        self.n: int = 0
        self.mean: float = 0.0
        self._m2: float = 0.0

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self._m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float:
        return self._m2 / self.n if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        return self.variance ** 0.5


class MinMaxScaler:
    """Map each feature from its known operating range onto ``[0, 1]``.

    Values outside the configured range are clamped, so a learner never sees an
    out-of-distribution input even on an unusually aggressive drive cycle.
    """

    def __init__(self, ranges: Sequence[tuple[float, float]] = FEATURE_RANGES) -> None:
        self.ranges: tuple[tuple[float, float], ...] = tuple(ranges)
        # Pre-compute spans, guarding against degenerate zero-width ranges.
        self._spans = tuple((hi - lo) or 1.0 for lo, hi in self.ranges)

    def transform(self, vector: Sequence[float]) -> tuple[float, ...]:
        out = []
        for x, (lo, _hi), span in zip(vector, self.ranges, self._spans):
            t = (x - lo) / span
            out.append(0.0 if t < 0.0 else 1.0 if t > 1.0 else t)
        return tuple(out)

    def inverse_transform(self, vector: Sequence[float]) -> tuple[float, ...]:
        return tuple(
            lo + t * span
            for t, (lo, _hi), span in zip(vector, self.ranges, self._spans)
        )


@dataclass
class OnlineStandardizer:
    """Adaptive z-score scaler that learns each feature's range as data streams.

    Call :meth:`partial_fit` once per observation (increment by 1). Until a
    feature has any variance it is passed through centred only, never divided by
    zero.
    """

    n_features: int
    _stats: list[RunningStats] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self._stats:
            self._stats = [RunningStats() for _ in range(self.n_features)]

    def partial_fit(self, vector: Sequence[float]) -> "OnlineStandardizer":
        for stat, x in zip(self._stats, vector):
            stat.update(x)
        return self

    def transform(self, vector: Sequence[float]) -> tuple[float, ...]:
        out = []
        for stat, x in zip(self._stats, vector):
            std = stat.std
            out.append((x - stat.mean) / std if std > 1e-9 else x - stat.mean)
        return tuple(out)

    def fit_transform(self, vector: Sequence[float]) -> tuple[float, ...]:
        """Update statistics with ``vector`` then return its scaled form."""
        self.partial_fit(vector)
        return self.transform(vector)


def make_minmax_scaler() -> MinMaxScaler:
    """Default scaler keyed to the study's documented feature ranges."""
    return MinMaxScaler(FEATURE_RANGES)
