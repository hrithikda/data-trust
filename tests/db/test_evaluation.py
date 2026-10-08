"""Detector evaluation executed for real in a dedicated sandbox schema.

These are the regression tests for the baseline detector's three held-out errors: each must stay
wrong for the baseline (history is not rewritten) and right for the improved detector, for the
documented reason.
"""

import json

import pytest
from psycopg import sql

from datatrust.evaluation.runner import EvaluationResult, EvaluationRunner
from tests.conftest import EVALUATION_DIR

pytestmark = pytest.mark.db

SCHEMA = "datatrust_test_eval"


@pytest.fixture(scope="module")
def results(db, settings, dbt_target_dir) -> dict[tuple[str, str], EvaluationResult]:
    runner = EvaluationRunner(db, settings.model_copy(update={"eval_schema": SCHEMA}))
    try:
        yield {(suite, detector): runner.evaluate(suite, detector, persist=False)
               for suite in ("holdout", "regression") for detector in ("baseline", "improved")}
    finally:
        db.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(SCHEMA)))


def counts(result: EvaluationResult) -> tuple[int, int, int, int]:
    m = result.matrix
    return m.true_positives, m.false_negatives, m.false_positives, m.true_negatives


def by_case(result: EvaluationResult):
    return {p.case_id: p for p in result.predictions}


def test_baseline_holdout_confusion_matrix(results):
    result = results["holdout", "baseline"]
    assert counts(result) == (28, 2, 1, 29)
    assert {(p.case_id, p.outcome) for p in result.errors()} == {("HO-036", "FN"), ("HO-050", "FN"),
                                                                 ("HO-059", "FP")}


def test_improved_holdout_confusion_matrix(results):
    assert counts(results["holdout", "improved"]) == (30, 0, 0, 30)


@pytest.mark.parametrize(("case_id", "baseline_outcome", "improved_rules"), [
    ("HO-050", "FN", ["refunds_cumulative_not_exceeding_payment"]),  # partial refunds summing past the capture
    ("HO-036", "FN", ["payments_customer_matches_order"]),           # payment customer != order customer
    ("HO-059", "FP", []),                                            # legitimate $0 support replacement
])
def test_baseline_errors_are_fixed_by_the_improved_rules(results, case_id, baseline_outcome, improved_rules):
    baseline = by_case(results["holdout", "baseline"])[case_id]
    improved = by_case(results["holdout", "improved"])[case_id]
    assert baseline.outcome == baseline_outcome
    assert improved.outcome in ("TP", "TN")
    assert improved.triggered_rules == improved_rules
    if case_id == "HO-059":
        assert baseline.triggered_rules == ["payments_succeeded_amount_positive"]


def test_improvements_do_not_cost_any_previously_correct_case(results):
    baseline, improved = by_case(results["holdout", "baseline"]), by_case(results["holdout", "improved"])
    regressions = [cid for cid, p in baseline.items() if p.outcome in ("TP", "TN") and
                   improved[cid].outcome not in ("TP", "TN")]
    assert regressions == []


def test_regression_suite(results):
    assert counts(results["regression", "improved"]) == (6, 0, 0, 4)
    baseline = results["regression", "baseline"]
    assert counts(baseline) == (3, 3, 1, 3)
    # Cumulative refunds (RG-001, RG-002) and the split-tender customer mismatch (RG-003) are missed; the
    # $0 two-line replacement (RG-008) is flagged. RG-004, a $0 capture on a paid order, is caught by the
    # baseline's blunt amount > 0 rule; the improved rule must keep catching it.
    assert {p.case_id for p in baseline.errors()} == {"RG-001", "RG-002", "RG-003", "RG-008"}
    assert by_case(results["regression", "improved"])["RG-004"].triggered_rules == ["payments_succeeded_amount_valid"]


@pytest.mark.parametrize("name", ["holdout_baseline", "holdout_improved", "regression_baseline",
                                  "regression_improved"])
def test_committed_result_snapshots_match_a_fresh_run(results, name):
    """The JSON evidence in evaluation/results must be reproducible, not edited by hand."""
    suite, detector = name.split("_")
    snapshot = json.loads((EVALUATION_DIR / "results" / f"{name}.json").read_text())
    fresh = results[suite, detector].as_dict()
    assert snapshot["cases_hash"] == fresh["cases_hash"]
    assert snapshot["confusion_matrix"] == fresh["confusion_matrix"]
    assert snapshot["predictions"] == fresh["predictions"]
