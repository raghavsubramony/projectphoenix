"""ml_study - an incremental learning study for the Phoenix Unified AI.

A pure standard-library ML library for **gathering** the powertrain's decision
telemetry (across its varying physical data ranges) and **utilizing** it to learn
the Unified-AI control policy one sample at a time. It is deliberately kept as a
*study*: a transparent, dependency-free scaffold that a production Unified AI can
later be mapped onto, not a black-box replacement for the rule-based controller.

Pipeline
--------
1. ``collect_dataset`` drives the :mod:`digital_twin` over drive cycles and
   records raw observations + the controller's tier decision and generation
   command (range-faithful gathering).
2. :class:`MinMaxScaler` / :class:`OnlineStandardizer` normalise the varying
   ranges (speed, demand, SoC) onto a common scale.
3. :class:`OnlineSoftmaxClassifier` / :class:`OnlineLinearRegressor` learn the
   decision and setpoint **by single-sample SGD** - the project's "increment by
   1, not by 10" rule.
4. :class:`LearningStudy` ties it together and exposes ``predict_decision`` - the
   runtime hook a future Unified AI would occupy.

Quick start::

    from ml_study import run_study
    study, report = run_study()
    print(report.report())
"""

from .features import (
    FeatureSpec,
    FEATURE_SPECS,
    FEATURE_NAMES,
    FEATURE_RANGES,
    N_FEATURES,
    observation,
    observation_from_step,
    describe_ranges,
)
from .scaling import (
    RunningStats,
    MinMaxScaler,
    OnlineStandardizer,
    make_minmax_scaler,
)
from .labels import (
    DECISION_LABELS,
    N_DECISIONS,
    LEARNING_INCREMENT,
    decision_from_step,
    label_name,
    clamp_increment,
)
from .dataset import Sample, Dataset
from .models import OnlineSoftmaxClassifier, OnlineLinearRegressor
from .collect import collect_dataset, default_target_max_w
from .study import LearningStudy, StudyReport, run_study
from .policy import LearnedController, learned_bodies

__all__ = [
    # features / ranges
    "FeatureSpec",
    "FEATURE_SPECS",
    "FEATURE_NAMES",
    "FEATURE_RANGES",
    "N_FEATURES",
    "observation",
    "observation_from_step",
    "describe_ranges",
    # scaling
    "RunningStats",
    "MinMaxScaler",
    "OnlineStandardizer",
    "make_minmax_scaler",
    # labels
    "DECISION_LABELS",
    "N_DECISIONS",
    "LEARNING_INCREMENT",
    "decision_from_step",
    "label_name",
    "clamp_increment",
    # dataset
    "Sample",
    "Dataset",
    # models
    "OnlineSoftmaxClassifier",
    "OnlineLinearRegressor",
    # collect
    "collect_dataset",
    "default_target_max_w",
    # study
    "LearningStudy",
    "StudyReport",
    "run_study",
    # policy (closed-loop controller)
    "LearnedController",
    "learned_bodies",
]
