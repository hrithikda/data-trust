import pytest

from datatrust.evaluation.metrics import ConfusionMatrix, outcome


def pairs(tp: int, fn: int, fp: int, tn: int) -> list[tuple[str, str]]:
    return ([("defective", "defective")] * tp + [("defective", "valid")] * fn
            + [("valid", "defective")] * fp + [("valid", "valid")] * tn)


def test_outcome_labels():
    assert outcome("defective", "defective") == "TP"
    assert outcome("defective", "valid") == "FN"
    assert outcome("valid", "defective") == "FP"
    assert outcome("valid", "valid") == "TN"
    with pytest.raises(ValueError):
        outcome("broken", "valid")


def test_metrics_are_computed_from_counts():
    m = ConfusionMatrix.from_pairs(pairs(28, 2, 1, 29))
    assert (m.true_positives, m.false_negatives, m.false_positives, m.true_negatives) == (28, 2, 1, 29)
    assert m.total == 60
    assert m.accuracy == 0.95
    assert m.precision == round(28 / 29, 4)
    assert m.recall == round(28 / 30, 4)
    assert m.specificity == round(29 / 30, 4)
    assert m.f1 == round(56 / 59, 4)
    assert m.as_dict()["f1_score"] == m.f1


def test_degenerate_matrices():
    nothing_flagged = ConfusionMatrix.from_pairs(pairs(0, 5, 0, 5))
    assert nothing_flagged.precision is None
    assert nothing_flagged.recall == 0.0
    assert nothing_flagged.f1 is None
    no_positives = ConfusionMatrix.from_pairs(pairs(0, 0, 1, 4))
    assert no_positives.precision == 0.0
    assert no_positives.recall is None
    assert ConfusionMatrix.from_pairs([]).accuracy is None
