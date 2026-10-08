"""Run a detector version over an evaluation suite and record every prediction.

A case is predicted *defective* when any rule of the detector fails on the case's records
(loaded alone into the sandbox together with the suite's shared reference data). Metrics are
computed from those predictions; nothing is hardcoded. Runs are append-only, so the baseline
results stay visible after the improved detector is evaluated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from datatrust.config import Settings
from datatrust.db import Database, json_default, to_jsonb
from datatrust.errors import DataTrustError
from datatrust.evaluation.cases import EvaluationSuite, load_suite
from datatrust.evaluation.metrics import POSITIVE, ConfusionMatrix, outcome
from datatrust.evaluation.sandbox import Sandbox
from datatrust.metadata.schema import record_pipeline_event
from datatrust.quality.engine import QualityEngine
from datatrust.quality.resolver import SchemaResolver
from datatrust.quality.rules import load_rules

logger = logging.getLogger(__name__)

SUITE_FILES = {"holdout": "holdout_cases.yml", "regression": "regression_cases.yml"}


@dataclass
class Prediction:
    case_id: str
    title: str
    expected: str
    predicted: str
    outcome: str
    triggered_rules: list[str]
    defect_category: str | None


@dataclass
class EvaluationResult:
    suite: str
    detector_version: str
    cases_hash: str
    predictions: list[Prediction]
    matrix: ConfusionMatrix
    eval_run_id: int | None = None

    def errors(self) -> list[Prediction]:
        return [p for p in self.predictions if p.outcome in ("FN", "FP")]

    def as_dict(self) -> dict:
        return {
            "suite": self.suite,
            "detector_version": self.detector_version,
            "cases_hash": self.cases_hash,
            "confusion_matrix": self.matrix.as_dict(),
            "errors": [p.__dict__ for p in self.errors()],
            "predictions": [p.__dict__ for p in self.predictions],
        }


class EvaluationRunner:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def suite_path(self, suite: str) -> Path:
        if suite not in SUITE_FILES:
            raise DataTrustError(f"Unknown suite '{suite}'; expected one of {sorted(SUITE_FILES)}")
        return self.settings.evaluation_dir / SUITE_FILES[suite]

    def evaluate(self, suite_name: str, detector_version: str, persist: bool = True) -> EvaluationResult:
        suite = load_suite(self.suite_path(suite_name))
        rules = load_rules(self.settings.config_dir / "quality_rules.yml", detector_version)
        sandbox = Sandbox(self.db, self.settings.eval_schema, self.settings.dbt_target_dir)
        sandbox.validate(suite.context, f"{suite_name} context")
        for case in suite.cases:
            sandbox.validate(case.records, case.id)
        sandbox.create()
        engine = QualityEngine(self.db, SchemaResolver(self.settings.eval_schema), sample_size=0)

        predictions: list[Prediction] = []
        for case in suite.cases:
            sandbox.load(suite.context, case.records)
            outcomes = engine.execute(rules, suite.reference_ts)
            errored = [o.rule.key for o in outcomes if o.status == "error"]
            if errored:
                raise DataTrustError(f"Rules errored on case {case.id}: {errored}")
            triggered = sorted(o.rule.key for o in outcomes if o.failed)
            predicted = POSITIVE if triggered else "valid"
            predictions.append(Prediction(case.id, case.title, case.expected, predicted,
                                          outcome(case.expected, predicted), triggered, case.defect_category))

        matrix = ConfusionMatrix.from_pairs((p.expected, p.predicted) for p in predictions)
        result = EvaluationResult(suite_name, detector_version, suite.cases_hash(), predictions, matrix)
        logger.info("%s / %s: TP=%d FN=%d FP=%d TN=%d", suite_name, detector_version, matrix.true_positives,
                    matrix.false_negatives, matrix.false_positives, matrix.true_negatives)
        if persist:
            result.eval_run_id = self._persist(suite, result)
            self._write_snapshot(result)
            record_pipeline_event(self.db, "evaluation", "succeeded",
                                  {"suite": suite_name, "detector": detector_version, **matrix.as_dict()})
        return result

    def _persist(self, suite: EvaluationSuite, result: EvaluationResult) -> int:
        self.db.require_metadata("evaluation_cases", "evaluation_runs")
        m = result.matrix
        with self.db.transaction() as cur:
            for case in suite.cases:
                cur.execute("SELECT content_hash FROM evaluation_cases WHERE case_id = %s", (case.id,))
                existing = cur.fetchone()
                if existing and existing["content_hash"] != case.content_hash():
                    logger.warning("Case %s changed since it was first recorded; earlier runs keep their cases_hash",
                                   case.id)
                cur.execute(
                    """
                    INSERT INTO evaluation_cases (case_id, suite, title, description, expected_label, defect_category,
                                                  payload, content_hash)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (case_id) DO UPDATE SET suite = EXCLUDED.suite, title = EXCLUDED.title,
                        description = EXCLUDED.description, expected_label = EXCLUDED.expected_label,
                        defect_category = EXCLUDED.defect_category, payload = EXCLUDED.payload,
                        content_hash = EXCLUDED.content_hash
                    """,
                    (case.id, suite.suite, case.title, case.description, case.expected, case.defect_category,
                     to_jsonb(case.records), case.content_hash()),
                )
            metrics = m.metrics()
            cur.execute(
                """
                INSERT INTO evaluation_runs (suite, detector_version, case_count, true_positives, false_negatives,
                    false_positives, true_negatives, accuracy, precision_score, recall, f1_score, specificity, cases_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING eval_run_id
                """,
                (result.suite, result.detector_version, m.total, m.true_positives, m.false_negatives,
                 m.false_positives, m.true_negatives, metrics["accuracy"], metrics["precision"], metrics["recall"],
                 metrics["f1_score"], metrics["specificity"], result.cases_hash),
            )
            run_id = cur.fetchone()["eval_run_id"]
            cur.executemany(
                """INSERT INTO evaluation_predictions (eval_run_id, case_id, expected_label, predicted_label, outcome,
                                                       triggered_rules) VALUES (%s, %s, %s, %s, %s, %s)""",
                [(run_id, p.case_id, p.expected, p.predicted, p.outcome, p.triggered_rules) for p in result.predictions],
            )
        return run_id

    def _write_snapshot(self, result: EvaluationResult) -> Path:
        """Commit-friendly copy of the latest result for each suite/detector (evidence in the repo)."""
        directory = self.settings.evaluation_dir / "results"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{result.suite}_{result.detector_version}.json"
        path.write_text(json.dumps(result.as_dict(), indent=2, default=json_default) + "\n", encoding="utf-8")
        return path
