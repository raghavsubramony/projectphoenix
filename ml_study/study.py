"""LearningStudy: incrementally learn the Unified-AI decision policy.

This is the *utilization* half of the library and the heart of the study. It
takes a gathered :class:`~ml_study.dataset.Dataset`, scales the varying-range
observations, and trains two online models **one sample at a time** to reproduce
the controller's decisions:

* a softmax classifier for the discrete tier decision, and
* a linear regressor for the (normalised) generation setpoint.

Because training is strictly incremental (batch size one, ``steps += 1`` per
sample), the headline quality metric is *prequential* accuracy - each sample is
**predicted before it is learned from**, the standard honest measure for online
learners and a direct readout of how the policy improves as data streams in.

The fitted study exposes :meth:`predict_decision`, the hook a future Unified AI
would call at runtime: raw observation in, ``(tier label, generation watts)``
out - drop-in comparable to the rule-based controller.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .dataset import Dataset
from .features import N_FEATURES
from .labels import (
    DECISION_LABELS,
    LEARNING_INCREMENT,
    N_DECISIONS,
    clamp_increment,
    label_name,
)
from .models import OnlineLinearRegressor, OnlineSoftmaxClassifier
from .scaling import MinMaxScaler, make_minmax_scaler


@dataclass
class StudyReport:
    """Outcome of an incremental learning run."""

    samples_seen: int
    prequential_accuracy: float          # online test-then-train accuracy
    holdout_accuracy: float              # accuracy on an unseen split (or 0)
    regression_mae_w: float              # mean abs generation error, watts
    class_distribution: dict[str, int]
    learning_curve: list[tuple[int, float]] = field(default_factory=list)

    def report(self) -> str:
        lines = [
            "=== Unified-AI learning study (online, increment = "
            f"{LEARNING_INCREMENT}) ===",
            f"  Samples streamed     : {self.samples_seen}",
            f"  Prequential accuracy : {self.prequential_accuracy * 100:5.1f} % "
            "(predict-before-learn)",
            f"  Holdout accuracy     : {self.holdout_accuracy * 100:5.1f} % "
            "(unseen split)",
            f"  Generation MAE       : {self.regression_mae_w / 1000:5.2f} kW",
            "  Decision mix         : " + ", ".join(
                f"{k} {v}" for k, v in self.class_distribution.items()),
        ]
        if self.learning_curve:
            checkpoints = ", ".join(
                f"{n}:{acc * 100:.0f}%" for n, acc in self.learning_curve)
            lines.append(f"  Learning curve       : {checkpoints}")
        return "\n".join(lines)


class LearningStudy:
    """Holds the scaler + online models that map observations to decisions."""

    def __init__(self, scaler: MinMaxScaler | None = None,
                 target_max_w: float = 230_000.0,
                 learning_rate: float = 0.10, seed: int = 0) -> None:
        self.scaler = scaler or make_minmax_scaler()
        self.target_max_w = target_max_w
        self.classifier = OnlineSoftmaxClassifier(
            N_FEATURES, N_DECISIONS, learning_rate=learning_rate, seed=seed)
        self.regressor = OnlineLinearRegressor(
            N_FEATURES, learning_rate=learning_rate / 2.0, seed=seed)

    # --- training ----------------------------------------------------------

    def fit_incremental(self, train: Dataset, test: Dataset | None = None,
                        epochs: int = 1, shuffle: bool = True,
                        curve_points: int = 10) -> StudyReport:
        """Stream ``train`` through both models one sample at a time.

        Returns a :class:`StudyReport` with the prequential accuracy gathered
        during streaming plus optional holdout metrics on ``test``.
        """
        seen = 0
        correct = 0
        total = max(1, len(train) * epochs)
        interval = max(1, total // max(1, curve_points))
        curve: list[tuple[int, float]] = []

        for epoch in range(epochs):
            data = train.shuffled(seed=epoch).samples if shuffle else train.samples
            for sample in data:
                x = self.scaler.transform(sample.features)
                # Test-then-train: score the prediction before updating.
                if self.classifier.predict(x) == sample.decision:
                    correct += 1
                self.classifier.partial_fit(x, sample.decision)
                # Regression target normalised into [0, 1] by capacity.
                y_norm = min(1.0, max(0.0, sample.target_w / self.target_max_w))
                self.regressor.partial_fit(x, y_norm)
                seen += 1
                if seen % interval == 0:
                    curve.append((seen, correct / seen))

        holdout_acc = self.evaluate_accuracy(test) if test else 0.0
        mae = self.evaluate_regression_mae(test or train)
        return StudyReport(
            samples_seen=seen,
            prequential_accuracy=correct / seen if seen else 0.0,
            holdout_accuracy=holdout_acc,
            regression_mae_w=mae,
            class_distribution=train.class_distribution(),
            learning_curve=curve,
        )

    # --- evaluation --------------------------------------------------------

    def evaluate_accuracy(self, dataset: Dataset) -> float:
        """Fraction of correctly classified tier decisions (no learning)."""
        if not len(dataset):
            return 0.0
        correct = 0
        for sample in dataset:
            x = self.scaler.transform(sample.features)
            if self.classifier.predict(x) == sample.decision:
                correct += 1
        return correct / len(dataset)

    def evaluate_regression_mae(self, dataset: Dataset) -> float:
        """Mean absolute generation-power error in watts (no learning)."""
        if not len(dataset):
            return 0.0
        total = 0.0
        for sample in dataset:
            x = self.scaler.transform(sample.features)
            pred_w = max(0.0, self.regressor.predict(x)) * self.target_max_w
            total += abs(pred_w - sample.target_w)
        return total / len(dataset)

    # --- inference (the future Unified-AI hook) ----------------------------

    def predict_decision(self, observation) -> tuple[str, float]:
        """Map a raw observation to ``(tier label, generation watts)``.

        This is the runtime entry point a learned Unified AI would expose,
        directly comparable to the rule-based controller's decision.
        """
        x = self.scaler.transform(observation)
        cls = self.classifier.predict(x)
        gen_w = max(0.0, self.regressor.predict(x)) * self.target_max_w
        return label_name(cls), gen_w

    def predict_decision_id(self, observation) -> int:
        """Tier-decision class id for a raw observation."""
        return self.classifier.predict(self.scaler.transform(observation))

    def step_toward(self, previous_decision: int, observation) -> int:
        """Decision class clamped to a single-tier move from ``previous``.

        Applies the "increment by 1, not by 10" rule to the model's raw choice
        so escalation is smooth (EV -> T1 -> T2 -> T3) at the control boundary.
        """
        target = self.predict_decision_id(observation)
        return clamp_increment(previous_decision, target)


def run_study(twin_builder=None, cycles=None, epochs: int = 3,
              seed: int = 0) -> tuple["LearningStudy", StudyReport]:
    """Gather data from the twin and fit the study end to end.

    Convenience entry point: collect -> split -> incrementally fit -> report.
    Returns the fitted :class:`LearningStudy` and its :class:`StudyReport`.
    """
    from .collect import collect_dataset, default_target_max_w

    dataset = collect_dataset(twin_builder, cycles)
    train, test = dataset.split(train_fraction=0.8, seed=seed)
    study = LearningStudy(target_max_w=default_target_max_w(), seed=seed)
    report = study.fit_incremental(train, test, epochs=epochs)
    return study, report
