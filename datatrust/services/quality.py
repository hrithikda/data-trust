"""Read-side access to quality runs, results and rule definitions."""

from __future__ import annotations

from typing import Any

from datatrust.config import Settings
from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.quality.engine import QualityEngine
from datatrust.quality.resolver import WarehouseResolver
from datatrust.quality.rules import load_rules


class QualityViewService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def runs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT run_id, ruleset, as_of, trigger, started_at, rules_executed, rules_failed, rules_errored,
                      health_score::float AS health
               FROM quality_runs WHERE status = 'succeeded' ORDER BY as_of DESC, run_id DESC LIMIT %s""",
            (limit,))

    def latest_run_id(self) -> int:
        run_id = self.db.fetch_value(
            "SELECT run_id FROM quality_runs WHERE status = 'succeeded' ORDER BY as_of DESC, run_id DESC LIMIT 1")
        if run_id is None:
            raise DataTrustError("No quality runs yet. Run `datatrust quality backfill` or `datatrust pipeline`.")
        return run_id

    def results(self, run_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT r.rule_key, r.name, r.category, r.rule_type, r.severity, r.business_impact, r.column_names,
                   a.name AS asset, a.layer, t.name AS owner, qr.status, qr.records_scanned, qr.records_failed,
                   qr.failure_rate::float AS failure_rate, qr.critical_columns, qr.downstream_count,
                   qr.rule_score::float AS rule_score, qr.execution_ms, qr.error_message, qr.sample_failures,
                   i.issue_key
            FROM quality_results qr
            JOIN quality_rules r ON r.rule_id = qr.rule_id
            JOIN assets a ON a.asset_id = qr.asset_id
            LEFT JOIN teams t ON t.team_id = a.owner_team_id
            LEFT JOIN issues i ON i.rule_id = qr.rule_id AND i.last_run_id = qr.run_id
            WHERE qr.run_id = %s
            ORDER BY (qr.status = 'pass'), array_position(ARRAY['critical', 'high', 'medium', 'low'], r.severity),
                     qr.records_failed DESC, r.rule_key
            """,
            (run_id,))

    def category_trend(self) -> list[dict[str, Any]]:
        """Failing rules and records per category per run (stacked trend)."""
        return self.db.fetch_all(
            """SELECT q.as_of, r.category, count(*) FILTER (WHERE qr.status = 'fail') AS failing_rules,
                      coalesce(sum(qr.records_failed), 0) AS failing_records
               FROM quality_results qr JOIN quality_runs q ON q.run_id = qr.run_id
               JOIN quality_rules r ON r.rule_id = qr.rule_id
               WHERE q.status = 'succeeded'
               GROUP BY q.as_of, r.category ORDER BY q.as_of, r.category""")

    def asset_health(self, run_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT a.name AS asset, a.layer, t.name AS owner, count(*) AS rules,
                      count(*) FILTER (WHERE qr.status = 'fail') AS failing_rules,
                      round(100 * sum(qr.rule_score * qr.severity_weight)
                            / nullif(sum(qr.severity_weight) FILTER (WHERE qr.rule_score IS NOT NULL), 0), 1)::float
                          AS health
               FROM quality_results qr JOIN assets a ON a.asset_id = qr.asset_id
               LEFT JOIN teams t ON t.team_id = a.owner_team_id
               WHERE qr.run_id = %s GROUP BY a.name, a.layer, t.name ORDER BY health, a.name""",
            (run_id,))

    def rule_history(self, rule_key: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT q.as_of, q.run_id, qr.status, qr.records_scanned, qr.records_failed,
                      qr.failure_rate::float AS failure_rate, qr.execution_ms
               FROM quality_results qr JOIN quality_runs q ON q.run_id = qr.run_id
               JOIN quality_rules r ON r.rule_id = qr.rule_id
               WHERE r.rule_key = %s AND q.status = 'succeeded' ORDER BY q.as_of, q.run_id""",
            (rule_key,))

    def rule_catalog(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT r.rule_key, r.name, r.description, r.category, r.rule_type, r.severity, a.name AS asset,
                      r.column_names, r.rulesets, r.supersedes, r.rationale, r.business_impact
               FROM quality_rules r JOIN assets a ON a.asset_id = r.asset_id WHERE r.is_active
               ORDER BY r.category, r.rule_key""")

    def rule_sql(self, rule_key: str) -> str:
        """The failing-rows SQL the engine executes for a rule (for transparency in the UI)."""
        rule = next((r for r in load_rules(self.settings.config_dir / "quality_rules.yml") if r.key == rule_key), None)
        if rule is None:
            raise DataTrustError(f"Unknown rule '{rule_key}'")
        _, failing = QualityEngine(self.db, WarehouseResolver(self.db)).compile(rule)
        with self.db.connect() as conn:
            return failing.as_string(conn)
