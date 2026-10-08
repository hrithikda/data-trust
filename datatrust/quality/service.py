"""Warehouse quality runs: sync rules, execute, persist results, maintain issues."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from datatrust.config import Settings
from datatrust.db import Database, to_jsonb
from datatrust.errors import DataTrustError
from datatrust.impact.analysis import ImpactAnalyzer, MetadataContext
from datatrust.metadata.schema import record_pipeline_event
from datatrust.priority.scoring import PriorityModel
from datatrust.quality.engine import QualityEngine, RuleOutcome
from datatrust.quality.health import health_score, rule_score, severity_weight
from datatrust.quality.issues import IssueAssessment, IssueManager, assess
from datatrust.quality.resolver import WarehouseResolver
from datatrust.quality.rules import QualityRule, load_rules, validate_rules_against_metadata

logger = logging.getLogger(__name__)


@dataclass
class RunSummary:
    run_id: int
    ruleset: str
    as_of: datetime
    rules_executed: int
    rules_failed: int
    rules_errored: int
    health_score: float | None
    issue_actions: dict[str, int] = field(default_factory=dict)


class QualityService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.rules_path = settings.config_dir / "quality_rules.yml"
        self.priority_model = PriorityModel.from_file(settings.config_dir / "priority_model.yml")

    # ------------------------------------------------------------------ rules
    def sync_rules(self) -> dict[str, int]:
        """Upsert every rule (all rulesets) into quality_rules; deactivate rules removed from YAML."""
        self.db.require_metadata("quality_rules", "assets")
        rules = load_rules(self.rules_path)
        columns: dict[str, set[str]] = {}
        for row in self.db.fetch_all("SELECT a.name, c.column_name FROM assets a LEFT JOIN asset_columns c "
                                     "ON c.asset_id = a.asset_id AND c.is_active WHERE a.is_active"):
            columns.setdefault(row["name"], set())
            if row["column_name"]:
                columns[row["name"]].add(row["column_name"])
        if not columns:
            raise DataTrustError("No assets in the catalog yet; run `datatrust ingest` first.")
        validate_rules_against_metadata(rules, columns)
        ids: dict[str, int] = {}
        with self.db.transaction() as cur:
            for r in rules:
                cur.execute(
                    """
                    INSERT INTO quality_rules (rule_key, name, description, category, rule_type, asset_id, column_names,
                        severity, params, business_impact, rulesets, supersedes, rationale, is_active, updated_at)
                    SELECT %s, %s, %s, %s, %s, asset_id, %s, %s, %s, %s, %s, %s, %s, true, now()
                    FROM assets WHERE name = %s
                    ON CONFLICT (rule_key) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
                        category = EXCLUDED.category, rule_type = EXCLUDED.rule_type, asset_id = EXCLUDED.asset_id,
                        column_names = EXCLUDED.column_names, severity = EXCLUDED.severity, params = EXCLUDED.params,
                        business_impact = EXCLUDED.business_impact, rulesets = EXCLUDED.rulesets,
                        supersedes = EXCLUDED.supersedes, rationale = EXCLUDED.rationale, is_active = true,
                        updated_at = now()
                    RETURNING rule_id
                    """,
                    (r.key, r.name, r.description, r.category, r.type, r.columns, r.severity, to_jsonb(r.params),
                     r.business_impact, list(r.rulesets), r.supersedes, r.rationale, r.asset),
                )
                ids[r.key] = cur.fetchone()["rule_id"]
            cur.execute("UPDATE quality_rules SET is_active = false WHERE NOT (rule_id = ANY(%s))", (list(ids.values()),))
        return ids

    # ------------------------------------------------------------------ runs
    def run(self, ruleset: str | None = None, as_of: datetime | None = None, trigger: str = "manual",
            filter_by_load_time: bool = False) -> RunSummary:
        ruleset = ruleset or self.settings.ruleset
        as_of = as_of or self.settings.reference_ts
        rule_ids = self.sync_rules()
        rules = load_rules(self.rules_path, ruleset)
        engine = QualityEngine(self.db, WarehouseResolver(self.db))
        started = datetime.now()
        outcomes = engine.execute(rules, as_of, filter_by_load_time)
        return self._persist(ruleset, as_of, trigger, started, outcomes, rule_ids)

    def backfill(self, days: int, ruleset: str | None = None) -> list[RunSummary]:
        """Replay daily runs up to the reference date using row load times (oldest first)."""
        if not 1 <= days <= 90:
            raise DataTrustError("backfill days must be between 1 and 90")
        cutoff = self.settings.reference_date
        return [
            self.run(ruleset, datetime.combine(cutoff - timedelta(days=offset), time(23, 59, 59)), "backfill", True)
            for offset in range(days - 1, -1, -1)
        ]

    def _persist(self, ruleset: str, as_of: datetime, trigger: str, started: datetime,
                 outcomes: list[RuleOutcome], rule_ids: dict[str, int]) -> RunSummary:
        context = MetadataContext.load(self.db)
        analyzer = ImpactAnalyzer(context)
        critical = self._critical_columns()
        criticality = {r["name"]: r["criticality"] for r in self.db.fetch_all("SELECT name, criticality FROM assets")}
        assessments: dict[str, IssueAssessment] = {
            o.rule.key: assess(o, analyzer, self.priority_model, critical, criticality[o.rule.asset])
            for o in outcomes if o.status != "error"
        }
        scores = [(o.rule.severity, rule_score(o.status, o.failure_rate)) for o in outcomes]
        health = health_score(scores)
        failed = sum(o.failed for o in outcomes)
        errored = sum(o.status == "error" for o in outcomes)
        with self.db.transaction() as cur:
            cur.execute(
                """INSERT INTO quality_runs (ruleset, as_of, trigger, status, started_at, finished_at, rules_executed,
                                             rules_failed, rules_errored, health_score)
                   VALUES (%s, %s, %s, 'succeeded', %s, now(), %s, %s, %s, %s) RETURNING run_id""",
                (ruleset, as_of, trigger, started, len(outcomes), failed, errored, health),
            )
            run_id = cur.fetchone()["run_id"]
            for outcome, (_, score) in zip(outcomes, scores, strict=True):
                rule = outcome.rule
                assessment = assessments.get(rule.key)
                cur.execute(
                    """
                    INSERT INTO quality_results (run_id, rule_id, asset_id, status, records_scanned, records_failed,
                        failure_rate, sample_failures, critical_columns, downstream_count, severity_weight, rule_score,
                        execution_ms, error_message)
                    SELECT %s, %s, asset_id, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s FROM assets WHERE name = %s
                    """,
                    (run_id, rule_ids[rule.key], outcome.status, outcome.records_scanned, outcome.records_failed,
                     round(outcome.failure_rate, 6), to_jsonb(outcome.samples),
                     [c for c, _ in assessment.critical_columns] if assessment else [],
                     assessment.impact.downstream_count if assessment else 0, severity_weight(rule.severity), score,
                     outcome.execution_ms, outcome.error, rule.asset),
                )
            actions = IssueManager(analyzer, self.priority_model).sync(
                cur, run_id, as_of, outcomes, rule_ids, assessments)
        record_pipeline_event(self.db, "quality_run", "succeeded",
                              {"run_id": run_id, "ruleset": ruleset, "as_of": as_of.isoformat(), **actions})
        logger.info("Run %s (%s, as of %s): %d/%d rules failed, health %s, issues %s",
                    run_id, ruleset, as_of, failed, len(outcomes), health, actions)
        return RunSummary(run_id, ruleset, as_of, len(outcomes), failed, errored, health, actions)

    def _critical_columns(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for row in self.db.fetch_all(
            "SELECT a.name, c.column_name, c.cde_category FROM asset_columns c JOIN assets a ON a.asset_id = c.asset_id "
            "WHERE c.is_critical AND c.is_active"
        ):
            result.setdefault(row["name"], {})[row["column_name"]] = row["cde_category"]
        return result

    def rules(self, ruleset: str | None = None) -> list[QualityRule]:
        return load_rules(self.rules_path, ruleset)
