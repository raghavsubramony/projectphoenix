"""Tests for the ml_study incremental learning library.

Pure-stdlib unittest (no third-party deps). Run with:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ml_study import (
    Dataset,
    LearningStudy,
    MinMaxScaler,
    OnlineLinearRegressor,
    OnlineSoftmaxClassifier,
    OnlineStandardizer,
    RunningStats,
    clamp_increment,
    collect_dataset,
    default_target_max_w,
    observation,
    run_study,
)
from ml_study.features import FEATURE_NAMES, N_FEATURES


class FeatureTest(unittest.TestCase):
    def test_observation_length_matches_schema(self) -> None:
        obs = observation(10.0, 40_000.0, 0.6, 0.7)
        self.assertEqual(len(obs), N_FEATURES)
        self.assertEqual(N_FEATURES, len(FEATURE_NAMES))


class ScalingTest(unittest.TestCase):
    def test_minmax_clamps_into_unit_interval(self) -> None:
        scaler = MinMaxScaler()
        # Deliberately out-of-range values must clamp to [0, 1].
        scaled = scaler.transform((999.0, 1e9, -5.0, 5.0))
        for v in scaled:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_running_stats_mean_and_variance(self) -> None:
        stats = RunningStats()
        for x in (2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0):
            stats.update(x)
        self.assertEqual(stats.n, 8)
        self.assertAlmostEqual(stats.mean, 5.0, places=6)
        # Population variance of the classic dataset is 4.0.
        self.assertAlmostEqual(stats.variance, 4.0, places=6)

    def test_online_standardizer_increments_by_one(self) -> None:
        std = OnlineStandardizer(N_FEATURES)
        std.partial_fit((1.0, 2.0, 0.3, 0.4))
        std.partial_fit((3.0, 6.0, 0.5, 0.6))
        # Two samples => each feature's running count is exactly 2.
        for stat in std._stats:
            self.assertEqual(stat.n, 2)


class ModelTest(unittest.TestCase):
    def test_classifier_learns_separable_problem(self) -> None:
        clf = OnlineSoftmaxClassifier(n_features=2, n_classes=2, seed=1)
        # Class 0 around (0,0), class 1 around (1,1): trivially separable.
        data = [((0.0, 0.1), 0), ((0.1, 0.0), 0),
                ((1.0, 0.9), 1), ((0.9, 1.0), 1)] * 100
        for x, y in data:
            clf.partial_fit(x, y)
        self.assertEqual(clf.predict((0.05, 0.05)), 0)
        self.assertEqual(clf.predict((0.95, 0.95)), 1)
        self.assertEqual(clf.steps, len(data))

    def test_regressor_reduces_error(self) -> None:
        reg = OnlineLinearRegressor(n_features=1, seed=1)
        # Target y = 0.5 * x.
        first = reg.partial_fit((1.0,), 0.5)
        for _ in range(2000):
            reg.partial_fit((1.0,), 0.5)
        last = reg.partial_fit((1.0,), 0.5)
        self.assertLess(last, first)
        self.assertAlmostEqual(reg.predict((1.0,)), 0.5, places=2)


class LabelTest(unittest.TestCase):
    def test_clamp_increment_limits_to_one_tier(self) -> None:
        # Cannot jump EV (0) straight to Tier 3 (3): clamp to a single step.
        self.assertEqual(clamp_increment(0, 3), 1)
        self.assertEqual(clamp_increment(3, 0), 2)
        self.assertEqual(clamp_increment(1, 2), 2)  # a legal single step


class DatasetTest(unittest.TestCase):
    def _toy_dataset(self) -> Dataset:
        ds = Dataset()
        ds.add((10.0, 40_000.0, 0.6, 0.7), 1, 40_000.0)
        ds.add((0.0, 0.0, 0.5, 0.7), 0, 0.0)
        return ds

    def test_add_and_class_distribution(self) -> None:
        ds = self._toy_dataset()
        self.assertEqual(len(ds), 2)
        dist = ds.class_distribution()
        self.assertEqual(dist["EV"], 1)
        self.assertEqual(dist["Tier 1"], 1)

    def test_csv_roundtrip(self) -> None:
        ds = self._toy_dataset()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.csv"
            ds.save_csv(path)
            loaded = Dataset.load_csv(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded.samples[0].decision, 1)
        self.assertAlmostEqual(loaded.samples[0].target_w, 40_000.0)
        self.assertEqual(loaded.feature_names, FEATURE_NAMES)


class StudyIntegrationTest(unittest.TestCase):
    """End-to-end: gather from the real twin and learn the policy online."""

    def test_collect_dataset_is_nonempty(self) -> None:
        ds = collect_dataset()
        self.assertGreater(len(ds), 0)
        # Every row carries the full feature schema.
        self.assertEqual(len(ds.samples[0].features), N_FEATURES)

    def test_study_learns_better_than_chance(self) -> None:
        study, report = run_study(epochs=3, seed=0)
        # Four decision classes => 25% is chance. The policy is highly
        # learnable from the rule-based controller, so demand a clear margin.
        self.assertGreater(report.prequential_accuracy, 0.6)
        self.assertGreater(report.holdout_accuracy, 0.6)
        # Generation MAE should be a sane fraction of full ATPE capacity.
        self.assertLess(report.regression_mae_w, default_target_max_w())

    def test_predict_decision_hook(self) -> None:
        study, _ = run_study(epochs=2, seed=0)
        label, gen_w = study.predict_decision(observation(0.0, 0.0, 0.6, 0.7))
        self.assertIn(label, {"EV", "Tier 1", "Tier 2", "Tier 3"})
        self.assertGreaterEqual(gen_w, 0.0)


if __name__ == "__main__":
    unittest.main()
