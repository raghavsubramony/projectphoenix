"""Dataset: the gathered, range-faithful study corpus.

A :class:`Dataset` is an append-only stream of :class:`Sample` rows, each pairing
a **raw** observation vector (kept in its native, varying ranges - see
:mod:`ml_study.features`) with the decision the controller actually made and the
generation power it commanded. Storing raw values keeps the corpus reusable: any
scaler or model can be applied later when mapping a Unified AI onto it.

The container is intentionally minimal and pure standard library - it gathers,
persists (CSV), splits and shuffles, nothing more.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .features import FEATURE_NAMES, N_FEATURES
from .labels import DECISION_LABELS


@dataclass(frozen=True)
class Sample:
    """One gathered observation paired with its supervised targets.

    Attributes:
        features:  raw observation vector (native units / ranges).
        decision:  tier-decision class id (classification target).
        target_w:  realised generation power in watts (regression target).
    """

    features: tuple[float, ...]
    decision: int
    target_w: float


class Dataset:
    """An append-only collection of :class:`Sample` rows."""

    def __init__(self, feature_names: Sequence[str] = FEATURE_NAMES) -> None:
        self.feature_names: tuple[str, ...] = tuple(feature_names)
        self.samples: list[Sample] = []

    # --- gathering ---------------------------------------------------------

    def add(self, features: Sequence[float], decision: int,
            target_w: float) -> None:
        """Append one row (advances the corpus by exactly one sample)."""
        if len(features) != len(self.feature_names):
            raise ValueError(
                f"expected {len(self.feature_names)} features, got {len(features)}"
            )
        self.samples.append(Sample(tuple(features), int(decision), float(target_w)))

    def extend(self, rows: Iterable[Sample]) -> None:
        self.samples.extend(rows)

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self) -> Iterator[Sample]:
        return iter(self.samples)

    # --- inspection --------------------------------------------------------

    def class_distribution(self) -> dict[str, int]:
        """Count of samples per decision class (label-keyed)."""
        counts = {name: 0 for name in DECISION_LABELS}
        for s in self.samples:
            counts[DECISION_LABELS[s.decision]] += 1
        return counts

    # --- shuffling / splitting --------------------------------------------

    def shuffled(self, seed: int = 0) -> "Dataset":
        """Return a new dataset with rows shuffled (original untouched)."""
        rng = random.Random(seed)
        order = list(self.samples)
        rng.shuffle(order)
        out = Dataset(self.feature_names)
        out.samples = order
        return out

    def split(self, train_fraction: float = 0.8,
              seed: int = 0) -> tuple["Dataset", "Dataset"]:
        """Shuffle then split into (train, test) datasets."""
        if not 0.0 < train_fraction < 1.0:
            raise ValueError("train_fraction must be in (0, 1)")
        shuffled = self.shuffled(seed)
        cut = int(len(shuffled) * train_fraction)
        train = Dataset(self.feature_names)
        test = Dataset(self.feature_names)
        train.samples = shuffled.samples[:cut]
        test.samples = shuffled.samples[cut:]
        return train, test

    # --- persistence -------------------------------------------------------

    def save_csv(self, path: str | Path) -> None:
        """Write the corpus to CSV (header = feature names + decision/target)."""
        path = Path(path)
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([*self.feature_names, "decision", "target_w"])
            for s in self.samples:
                writer.writerow([*s.features, s.decision, s.target_w])

    @classmethod
    def load_csv(cls, path: str | Path) -> "Dataset":
        """Read a corpus previously written by :meth:`save_csv`."""
        path = Path(path)
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader)
            feature_names = tuple(header[:-2])
            ds = cls(feature_names)
            n = len(feature_names)
            for row in reader:
                if not row:
                    continue
                features = tuple(float(v) for v in row[:n])
                decision = int(row[n])
                target_w = float(row[n + 1])
                ds.samples.append(Sample(features, decision, target_w))
        return ds
