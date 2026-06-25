"""Incremental, one-sample-at-a-time online learners (pure standard library).

These are deliberately small, transparent models trained by **stochastic
gradient descent with a batch size of one** - the project's "learning increment
normally by 1, not by 10" rule made literal. Each call to ``partial_fit`` sees a
single sample, takes one gradient step and advances ``steps`` by exactly one.
That makes the learning behaviour streamable inside the control loop and easy to
reason about when mapping a Unified AI onto the study later.

Two models cover the two targets of the study:

* :class:`OnlineSoftmaxClassifier` - multinomial logistic regression predicting
  the discrete tier decision (EV / Tier 1 / Tier 2 / Tier 3).
* :class:`OnlineLinearRegressor` - linear regression predicting the (normalised)
  generation power setpoint.

No third-party dependencies: vectors are plain Python lists/tuples.
"""

from __future__ import annotations

import math
import random
from typing import Sequence


def _dot(w: Sequence[float], x: Sequence[float]) -> float:
    return sum(wi * xi for wi, xi in zip(w, x))


class OnlineSoftmaxClassifier:
    """Multinomial logistic regression trained one sample at a time."""

    def __init__(self, n_features: int, n_classes: int,
                 learning_rate: float = 0.10, l2: float = 1e-4,
                 seed: int = 0) -> None:
        self.n_features = n_features
        self.n_classes = n_classes
        self.lr = learning_rate
        self.l2 = l2
        rng = random.Random(seed)
        self.w: list[list[float]] = [
            [rng.uniform(-0.01, 0.01) for _ in range(n_features)]
            for _ in range(n_classes)
        ]
        self.b: list[float] = [0.0 for _ in range(n_classes)]
        self.steps: int = 0

    def predict_proba(self, x: Sequence[float]) -> list[float]:
        logits = [_dot(self.w[k], x) + self.b[k] for k in range(self.n_classes)]
        m = max(logits)
        exps = [math.exp(z - m) for z in logits]
        total = sum(exps)
        return [e / total for e in exps]

    def predict(self, x: Sequence[float]) -> int:
        proba = self.predict_proba(x)
        best, best_p = 0, proba[0]
        for k in range(1, self.n_classes):
            if proba[k] > best_p:
                best, best_p = k, proba[k]
        return best

    def partial_fit(self, x: Sequence[float], y: int) -> float:
        """One SGD step on a single sample. Returns the cross-entropy loss.

        Learning increment is exactly one sample (``steps += 1``).
        """
        proba = self.predict_proba(x)
        for k in range(self.n_classes):
            grad = proba[k] - (1.0 if k == y else 0.0)
            wk = self.w[k]
            for j in range(self.n_features):
                wk[j] -= self.lr * (grad * x[j] + self.l2 * wk[j])
            self.b[k] -= self.lr * grad
        self.steps += 1
        return -math.log(max(proba[y], 1e-12))


class OnlineLinearRegressor:
    """Linear regression trained by single-sample SGD.

    Intended for targets pre-scaled into a small range (e.g. generation power
    normalised to ``[0, 1]``); keep raw watt-scale targets out so the unit step
    stays stable.
    """

    def __init__(self, n_features: int, learning_rate: float = 0.05,
                 l2: float = 1e-4, seed: int = 0) -> None:
        self.n_features = n_features
        self.lr = learning_rate
        self.l2 = l2
        rng = random.Random(seed)
        self.w: list[float] = [rng.uniform(-0.01, 0.01) for _ in range(n_features)]
        self.b: float = 0.0
        self.steps: int = 0

    def predict(self, x: Sequence[float]) -> float:
        return _dot(self.w, x) + self.b

    def partial_fit(self, x: Sequence[float], y: float) -> float:
        """One SGD step on a single sample. Returns the squared error."""
        error = self.predict(x) - y
        for j in range(self.n_features):
            self.w[j] -= self.lr * (error * x[j] + self.l2 * self.w[j])
        self.b -= self.lr * error
        self.steps += 1
        return error * error
