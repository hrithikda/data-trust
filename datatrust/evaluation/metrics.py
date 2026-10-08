"""Binary classification metrics for the detector ("defective" is the positive class)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass

POSITIVE, NEGATIVE = "defective", "valid"


def outcome(expected: str, predicted: str) -> str:
    """TP / FN / FP / TN for one prediction."""
    for label in (expected, predicted):
        if label not in (POSITIVE, NEGATIVE):
            raise ValueError(f"unknown label {label!r}")
    if expected == POSITIVE:
        return "TP" if predicted == POSITIVE else "FN"
    return "FP" if predicted == POSITIVE else "TN"


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


@dataclass(frozen=True)
class ConfusionMatrix:
    true_positives: int
    false_negatives: int
    false_positives: int
    true_negatives: int

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> ConfusionMatrix:
        """Build from (expected, predicted) label pairs."""
        counts = {"TP": 0, "FN": 0, "FP": 0, "TN": 0}
        for expected, predicted in pairs:
            counts[outcome(expected, predicted)] += 1
        return cls(counts["TP"], counts["FN"], counts["FP"], counts["TN"])

    @property
    def total(self) -> int:
        return self.true_positives + self.false_negatives + self.false_positives + self.true_negatives

    @property
    def accuracy(self) -> float | None:
        return _ratio(self.true_positives + self.true_negatives, self.total)

    @property
    def precision(self) -> float | None:
        return _ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> float | None:
        return _ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def specificity(self) -> float | None:
        return _ratio(self.true_negatives, self.true_negatives + self.false_positives)

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if not p or not r:
            return 0.0 if p is not None and r is not None else None
        # computed from counts, not rounded P/R, to avoid compounding rounding error
        return round(2 * self.true_positives / (2 * self.true_positives + self.false_positives + self.false_negatives), 4)

    def metrics(self) -> dict[str, float | None]:
        return {"accuracy": self.accuracy, "precision": self.precision, "recall": self.recall,
                "f1_score": self.f1, "specificity": self.specificity}

    def as_dict(self) -> dict:
        return {**asdict(self), "total": self.total, **self.metrics()}
