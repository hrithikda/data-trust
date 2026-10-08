"""Issue queue, investigation detail and triage actions."""

from __future__ import annotations

from typing import Any

from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.quality.issues import TRANSITIONS, add_issue_comment, change_issue_status

STATUSES = ("open", "investigating", "accepted", "resolved")


class IssueService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def queue(self, statuses: list[str] | None = None, bands: list[str] | None = None,
             owners: list[str] | None = None, assets: list[str] | None = None) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """
            SELECT i.issue_key, i.title, i.status, i.severity, i.business_criticality, i.priority_band,
                   i.priority_score::float AS priority_score, i.affected_records, i.affected_rate::float AS affected_rate,
                   i.records_scanned, a.name AS asset, a.layer, t.name AS owner, r.rule_key, r.category,
                   i.critical_columns, (i.impact_summary->>'downstream_count')::int AS downstream_count,
                   (i.impact_summary->>'impact_score')::float AS impact_score,
                   i.impact_summary->'exposures' AS exposures, i.first_detected_at, i.last_detected_at,
                   i.occurrences
            FROM issues i
            JOIN assets a ON a.asset_id = i.asset_id
            JOIN quality_rules r ON r.rule_id = i.rule_id
            LEFT JOIN teams t ON t.team_id = i.owner_team_id
            WHERE (%(statuses)s::text[] IS NULL OR i.status = ANY(%(statuses)s))
              AND (%(bands)s::text[] IS NULL OR i.priority_band = ANY(%(bands)s))
              AND (%(owners)s::text[] IS NULL OR t.name = ANY(%(owners)s))
              AND (%(assets)s::text[] IS NULL OR a.name = ANY(%(assets)s))
            ORDER BY i.priority_score DESC, i.issue_key
            """,
            {"statuses": statuses or None, "bands": bands or None, "owners": owners or None,
             "assets": assets or None},
        )

    def detail(self, issue_key: str) -> dict[str, Any]:
        issue = self.db.fetch_one(
            """
            SELECT i.*, i.priority_score::float AS priority_score, i.affected_rate::float AS affected_rate,
                   a.name AS asset, a.layer, a.description AS asset_description, a.criticality AS asset_criticality,
                   t.name AS owner, t.lead_name, t.email AS owner_email, t.slack_channel, t.on_call_rotation,
                   r.rule_key, r.name AS rule_name, r.description AS rule_description, r.category, r.rule_type,
                   r.business_impact, r.column_names, r.rulesets, r.rationale
            FROM issues i
            JOIN assets a ON a.asset_id = i.asset_id
            JOIN quality_rules r ON r.rule_id = i.rule_id
            LEFT JOIN teams t ON t.team_id = i.owner_team_id
            WHERE i.issue_key = %s
            """,
            (issue_key,),
        )
        if issue is None:
            raise DataTrustError(f"Issue {issue_key} not found")
        issue["allowed_transitions"] = sorted(TRANSITIONS[issue["status"]])
        issue["impacts"] = self.db.fetch_all(
            """SELECT a.name, a.asset_type, a.layer, a.criticality, t.name AS owner, ii.depth, ii.is_critical,
                      ii.path, a.is_financial_reporting, a.is_customer_facing, a.audience
               FROM issue_impacts ii JOIN assets a ON a.asset_id = ii.impacted_asset_id
               LEFT JOIN teams t ON t.team_id = a.owner_team_id
               WHERE ii.issue_id = %s ORDER BY ii.depth, a.name""",
            (issue["issue_id"],))
        issue["events"] = self.db.fetch_all(
            """SELECT e.event_type, e.from_status, e.to_status, e.actor, e.note, e.created_at, q.as_of AS run_as_of
               FROM issue_events e LEFT JOIN quality_runs q ON q.run_id = e.run_id
               WHERE e.issue_id = %s ORDER BY e.event_id""",
            (issue["issue_id"],))
        issue["history"] = self.db.fetch_all(
            """SELECT q.as_of, qr.status, qr.records_failed, qr.records_scanned
               FROM quality_results qr JOIN quality_runs q ON q.run_id = qr.run_id
               WHERE qr.rule_id = %s AND q.status = 'succeeded' ORDER BY q.as_of, q.run_id""",
            (issue["rule_id"],))
        issue["owner_contacts"] = self.db.fetch_all(
            """SELECT DISTINCT t.name, t.lead_name, t.email, t.slack_channel,
                      (t.team_id = %(owner)s) AS owns_broken_asset
               FROM issue_impacts ii JOIN assets a ON a.asset_id = ii.impacted_asset_id
               JOIN teams t ON t.team_id = a.owner_team_id
               WHERE ii.issue_id = %(issue)s
               UNION
               SELECT t.name, t.lead_name, t.email, t.slack_channel, true FROM teams t WHERE t.team_id = %(owner)s
               ORDER BY owns_broken_asset DESC, name""",
            {"owner": issue["owner_team_id"], "issue": issue["issue_id"]})
        return issue

    def keys(self) -> list[str]:
        return [r["issue_key"] for r in self.db.fetch_all(
            "SELECT issue_key FROM issues ORDER BY (status = 'resolved'), priority_score DESC")]

    def change_status(self, issue_key: str, status: str, actor: str, note: str | None) -> None:
        change_issue_status(self.db, issue_key, status, actor, note)

    def comment(self, issue_key: str, actor: str, note: str) -> None:
        add_issue_comment(self.db, issue_key, actor, note)

    def owners(self) -> list[str]:
        return [r["name"] for r in self.db.fetch_all("SELECT name FROM teams ORDER BY name")]
