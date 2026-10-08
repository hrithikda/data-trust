"""Evaluation history, comparisons and failure analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from datatrust.db import Database
from datatrust.errors import ConfigurationError, DataTrustError

ANALYSIS_FILE = "failure_analysis.yml"


class EvaluationService:
    def __init__(self, db: Database, evaluation_dir: Path | None = None) -> None:
        self.db = db
        self.evaluation_dir = evaluation_dir

    def documented_analysis(self) -> dict[str, dict[str, Any]]:
        """Written failure analysis per case id (``evaluation/failure_analysis.yml``)."""
        if self.evaluation_dir is None:
            return {}
        path = self.evaluation_dir / ANALYSIS_FILE
        if not path.exists():
            return {}
        try:
            cases = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cases", [])
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid {path}: {exc}") from exc
        return {c["case_id"]: c for c in cases}

    def runs(self) -> list[dict[str, Any]]:
        """Every evaluation run ever recorded (append-only history)."""
        return self.db.fetch_all(
            """SELECT eval_run_id, suite, detector_version, case_count, true_positives, false_negatives,
                      false_positives, true_negatives, accuracy::float AS accuracy,
                      precision_score::float AS precision, recall::float AS recall, f1_score::float AS f1_score,
                      specificity::float AS specificity, cases_hash, executed_at
               FROM evaluation_runs ORDER BY eval_run_id""")

    def latest(self, suite: str, detector: str) -> dict[str, Any] | None:
        return self.db.fetch_one(
            """SELECT eval_run_id, suite, detector_version, case_count, true_positives, false_negatives,
                      false_positives, true_negatives, accuracy::float AS accuracy,
                      precision_score::float AS precision, recall::float AS recall, f1_score::float AS f1_score,
                      specificity::float AS specificity, cases_hash, executed_at
               FROM evaluation_runs WHERE suite = %s AND detector_version = %s
               ORDER BY eval_run_id DESC LIMIT 1""",
            (suite, detector))

    def predictions(self, eval_run_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT p.case_id, c.title, c.description, c.defect_category, p.expected_label, p.predicted_label,
                      p.outcome, p.triggered_rules
               FROM evaluation_predictions p JOIN evaluation_cases c ON c.case_id = p.case_id
               WHERE p.eval_run_id = %s ORDER BY p.case_id""",
            (eval_run_id,))

    def case(self, case_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM evaluation_cases WHERE case_id = %s", (case_id,))
        if row is None:
            raise DataTrustError(f"Unknown evaluation case {case_id}")
        return row

    def failure_analysis(self, suite: str = "holdout") -> list[dict[str, Any]]:
        """Baseline errors with the improved detector's verdict and the rule changes that address them.

        The link from an error to its fix is the rule rationale, which cites the case id.
        """
        baseline = self.latest(suite, "baseline")
        improved = self.latest(suite, "improved")
        if baseline is None:
            raise DataTrustError(f"No baseline evaluation for suite '{suite}'. Run `datatrust evaluate`.")
        errors = [p for p in self.predictions(baseline["eval_run_id"]) if p["outcome"] in ("FN", "FP")]
        improved_by_case = ({p["case_id"]: p for p in self.predictions(improved["eval_run_id"])}
                            if improved else {})
        documented = self.documented_analysis()
        for error in errors:
            error["analysis"] = documented.get(error["case_id"])
            after = improved_by_case.get(error["case_id"])
            error["improved_outcome"] = after["outcome"] if after else None
            error["improved_triggered_rules"] = after["triggered_rules"] if after else []
            error["fixes"] = self.db.fetch_all(
                """SELECT rule_key, name, supersedes, rationale, rulesets FROM quality_rules
                   WHERE rationale LIKE %s ORDER BY rule_key""",
                (f"%{error['case_id']}%",))
            error["payload"] = self.case(error["case_id"])["payload"]
        return errors

    def regression_coverage(self) -> list[dict[str, Any]]:
        """Regression cases with their baseline and improved outcomes side by side."""
        baseline = self.latest("regression", "baseline")
        improved = self.latest("regression", "improved")
        if baseline is None or improved is None:
            return []
        before = {p["case_id"]: p for p in self.predictions(baseline["eval_run_id"])}
        rows = []
        for p in self.predictions(improved["eval_run_id"]):
            rows.append({**p, "baseline_outcome": before.get(p["case_id"], {}).get("outcome"),
                         "baseline_triggered_rules": before.get(p["case_id"], {}).get("triggered_rules", [])})
        return rows
