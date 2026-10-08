"""Platform overview: readiness, headline KPIs, health trend and the most important issues."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from datatrust.db import Database
from datatrust.errors import DatabaseUnavailableError

STEP_TABLES = {
    "assets": "ingest",
    "profile_runs": "profile",
    "quality_runs": "quality backfill",
    "evaluation_runs": "evaluate",
}


@dataclass
class Readiness:
    """What has been built so far; lets every page explain an empty state."""

    database: bool
    metadata_schema: bool
    counts: dict[str, int]
    message: str | None = None

    @property
    def missing_steps(self) -> list[str]:
        if not self.database:
            return ["start PostgreSQL"]
        if not self.metadata_schema:
            return ["init-db", *STEP_TABLES.values()]
        return [step for table, step in STEP_TABLES.items() if not self.counts.get(table)]

    @property
    def ready(self) -> bool:
        return not self.missing_steps

    def has(self, table: str) -> bool:
        return bool(self.counts.get(table))

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "missing_steps": self.missing_steps, "ready": self.ready}


def check_readiness(db: Database) -> Readiness:
    try:
        db.fetch_value("SELECT 1")
    except DatabaseUnavailableError as exc:
        return Readiness(False, False, {}, str(exc))
    if not db.table_exists("pipeline_events"):
        return Readiness(True, False, {}, "The DataTrust metadata schema has not been created yet.")
    counts = db.fetch_one(
        """SELECT (SELECT count(*) FROM assets WHERE is_active) AS assets,
                  (SELECT count(*) FROM profile_runs WHERE status = 'succeeded') AS profile_runs,
                  (SELECT count(*) FROM quality_runs WHERE status = 'succeeded') AS quality_runs,
                  (SELECT count(*) FROM issues) AS issues,
                  (SELECT count(*) FROM evaluation_runs) AS evaluation_runs"""
    )
    return Readiness(True, True, {k: int(v) for k, v in counts.items()})


LATEST_RUN = "(SELECT run_id FROM quality_runs WHERE status = 'succeeded' ORDER BY as_of DESC, run_id DESC LIMIT 1)"


class OverviewService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def readiness(self) -> Readiness:
        return check_readiness(self.db)

    def kpis(self) -> dict[str, Any]:
        row = self.db.fetch_one(
            f"""
            WITH runs AS (
                SELECT run_id, as_of, health_score, row_number() OVER (ORDER BY as_of DESC, run_id DESC) AS rn
                FROM quality_runs WHERE status = 'succeeded'
            )
            SELECT
                (SELECT count(*) FROM assets WHERE is_active AND asset_type IN ('source', 'model')) AS datasets,
                (SELECT count(*) FROM assets WHERE is_active AND asset_type = 'exposure') AS exposures,
                (SELECT count(*) FROM asset_columns WHERE is_active) AS columns,
                (SELECT count(*) FROM asset_columns WHERE is_active AND is_critical) AS critical_fields,
                (SELECT count(*) FROM quality_rules WHERE is_active) AS rules_defined,
                (SELECT count(*) FROM quality_results WHERE run_id = {LATEST_RUN}) AS rules_executed,
                (SELECT count(*) FROM quality_results WHERE run_id = {LATEST_RUN} AND status = 'fail') AS rules_failing,
                (SELECT count(*) FROM issues WHERE status IN ('open', 'investigating')) AS open_issues,
                (SELECT count(*) FROM issues WHERE status = 'accepted') AS accepted_issues,
                (SELECT count(*) FROM issues WHERE status IN ('open', 'investigating') AND priority_band = 'P1')
                    AS p1_issues,
                (SELECT count(*) FROM issues WHERE status IN ('open', 'investigating') AND severity = 'critical')
                    AS critical_issues,
                (SELECT health_score FROM runs WHERE rn = 1) AS health,
                (SELECT as_of FROM runs WHERE rn = 1) AS health_as_of,
                (SELECT health_score FROM runs WHERE rn = 8) AS health_week_ago,
                (SELECT count(*) FROM glossary_terms) AS glossary_terms,
                (SELECT count(*) FROM teams) AS teams
            """
        )
        result = dict(row)
        for key in ("health", "health_week_ago"):
            result[key] = float(result[key]) if result[key] is not None else None
        result["health_change_7d"] = (round(result["health"] - result["health_week_ago"], 2)
                                      if result["health"] is not None and result["health_week_ago"] is not None
                                      else None)
        return result

    def health_trend(self) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT run_id, as_of, ruleset, health_score::float AS health, rules_executed, rules_failed
               FROM quality_runs WHERE status = 'succeeded' ORDER BY as_of, run_id"""
        )

    def top_issues(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT i.issue_key, i.title, i.status, i.severity, i.priority_score::float AS priority_score,
                      i.priority_band, i.affected_records, a.name AS asset, t.name AS owner,
                      i.impact_summary->'exposures' AS exposures
               FROM issues i JOIN assets a ON a.asset_id = i.asset_id LEFT JOIN teams t ON t.team_id = i.owner_team_id
               WHERE i.status IN ('open', 'investigating')
               ORDER BY i.priority_score DESC, i.issue_key LIMIT %s""",
            (limit,),
        )

    def failing_assets(self, limit: int = 8) -> list[dict[str, Any]]:
        """Assets with failing rules in the latest run, with when each started failing."""
        return self.db.fetch_all(
            f"""
            SELECT a.name AS asset, a.layer, t.name AS owner, count(*) AS failing_rules,
                   sum(qr.records_failed) AS failing_records,
                   round(100 * sum(qr.rule_score * qr.severity_weight) / sum(qr.severity_weight), 1)::float AS health,
                   min(i.first_detected_at) AS failing_since
            FROM quality_results qr
            JOIN assets a ON a.asset_id = qr.asset_id
            LEFT JOIN teams t ON t.team_id = a.owner_team_id
            LEFT JOIN issues i ON i.rule_id = qr.rule_id AND i.status <> 'resolved'
            WHERE qr.run_id = {LATEST_RUN} AND qr.status = 'fail'
            GROUP BY a.name, a.layer, t.name
            ORDER BY failing_since DESC NULLS LAST, failing_rules DESC
            LIMIT %s
            """,
            (limit,),
        )

    def issue_distribution(self) -> list[dict[str, Any]]:
        """Unresolved issues by owner, severity, band and category (for charts)."""
        return self.db.fetch_all(
            """SELECT i.issue_key, i.status, i.severity, i.priority_band, i.priority_score::float AS priority_score,
                      i.affected_records, t.name AS owner, r.category
               FROM issues i JOIN quality_rules r ON r.rule_id = i.rule_id
               LEFT JOIN teams t ON t.team_id = i.owner_team_id
               WHERE i.status <> 'resolved'"""
        )

    def pipeline_events(self, limit: int = 15) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            "SELECT step, status, details, created_at FROM pipeline_events ORDER BY event_id DESC LIMIT %s", (limit,))
