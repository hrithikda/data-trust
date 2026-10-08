"""Turn failing rule results into tracked, prioritised quality issues.

Lifecycle per rule (at most one unresolved issue per rule, enforced by a partial unique index):
  fail, no unresolved issue      -> create (or reopen the latest resolved issue for the rule)
  fail, unresolved issue exists  -> refresh counts, samples, impact and priority
  pass, unresolved issue exists  -> auto-resolve
Manual transitions (investigating / accepted / resolved) are validated and audited.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import psycopg

from datatrust.db import to_jsonb
from datatrust.errors import DataTrustError
from datatrust.impact.analysis import ImpactAnalyzer, ImpactReport
from datatrust.priority.scoring import IssueSignals, PriorityModel, PriorityResult
from datatrust.quality.engine import RuleOutcome

logger = logging.getLogger(__name__)

UNRESOLVED = ("open", "investigating", "accepted")
TRANSITIONS: dict[str, set[str]] = {
    "open": {"investigating", "accepted", "resolved"},
    "investigating": {"open", "accepted", "resolved"},
    "accepted": {"open", "resolved"},
    "resolved": {"open"},
}
ROW_LEVEL_CATEGORIES = {"duplicate_identifier"}
"""Defects that duplicate whole rows propagate to every consumer, regardless of columns."""


@dataclass
class IssueAssessment:
    """Impact + priority for one failing outcome (also used by the UI for what-if analysis)."""

    impact: ImpactReport
    priority: PriorityResult
    critical_columns: list[tuple[str, str]]


def assess(outcome: RuleOutcome, analyzer: ImpactAnalyzer, model: PriorityModel,
           critical_columns: dict[str, dict[str, str]], criticality: str) -> IssueAssessment:
    rule = outcome.rule
    cdes = [(c, critical_columns.get(rule.asset, {})[c]) for c in rule.columns if c in critical_columns.get(rule.asset, {})]
    impact = analyzer.analyze(rule.asset, rule.columns, row_level=rule.category in ROW_LEVEL_CATEGORIES)
    signals = IssueSignals(severity=rule.severity, records_failed=outcome.records_failed,
                           records_scanned=outcome.records_scanned, asset_criticality=criticality,
                           critical_columns=cdes)
    return IssueAssessment(impact=impact, priority=model.score(signals, impact), critical_columns=cdes)


class IssueManager:
    def __init__(self, analyzer: ImpactAnalyzer, model: PriorityModel) -> None:
        self.analyzer = analyzer
        self.model = model

    def sync(self, cur: psycopg.Cursor, run_id: int, as_of: datetime, outcomes: list[RuleOutcome],
             rule_ids: dict[str, int], assessments: dict[str, IssueAssessment]) -> dict[str, int]:
        """Apply one run's outcomes to the issue table. Returns counts by action."""
        counts = {"created": 0, "updated": 0, "reopened": 0, "auto_resolved": 0}
        for outcome in outcomes:
            if outcome.status == "error":
                continue
            rule_id = rule_ids[outcome.rule.key]
            cur.execute("SELECT * FROM issues WHERE rule_id = %s AND status = ANY(%s)", (rule_id, list(UNRESOLVED)))
            current = cur.fetchone()
            if outcome.status == "pass":
                if current:
                    self._transition(cur, current["issue_id"], current["status"], "resolved", "datatrust",
                                     f"Rule passed in run {run_id}", run_id, "auto_resolved", as_of)
                    counts["auto_resolved"] += 1
                continue
            assessment = assessments[outcome.rule.key]
            if current:
                self._refresh(cur, current, outcome, assessment, run_id, as_of)
                counts["updated"] += 1
                continue
            cur.execute("SELECT * FROM issues WHERE rule_id = %s AND status = 'resolved' ORDER BY resolved_at DESC LIMIT 1",
                        (rule_id,))
            previous = cur.fetchone()
            if previous:
                self._transition(cur, previous["issue_id"], "resolved", "open", "datatrust",
                                 f"Rule failed again in run {run_id}", run_id, "reopened", None)
                cur.execute("SELECT * FROM issues WHERE issue_id = %s", (previous["issue_id"],))
                self._refresh(cur, cur.fetchone(), outcome, assessment, run_id, as_of, log_event=False)
                counts["reopened"] += 1
            else:
                self._create(cur, outcome, assessment, rule_id, run_id, as_of)
                counts["created"] += 1
        return counts

    # ------------------------------------------------------------------ writes
    def _fields(self, outcome: RuleOutcome, assessment: IssueAssessment) -> dict:
        rule = outcome.rule
        impact = assessment.impact
        consumers = ", ".join(a.name for a in impact.exposures) or "no registered business outputs"
        description = (
            f"{rule.description} {outcome.records_failed:,} of {outcome.records_scanned:,} rows "
            f"({outcome.failure_rate:.2%}) fail. Business impact: {rule.business_impact} "
            f"Downstream: {impact.downstream_count} asset(s), reaching {consumers}."
        )
        return {
            "title": f"{rule.name} - {outcome.records_failed:,} failing rows in {rule.asset}",
            "description": description,
            "severity": rule.severity,
            "affected_records": outcome.records_failed,
            "affected_rate": round(outcome.failure_rate, 6),
            "records_scanned": outcome.records_scanned,
            "sample_failures": to_jsonb(outcome.samples),
            "critical_columns": [c for c, _ in assessment.critical_columns],
            "priority_score": assessment.priority.score,
            "priority_band": assessment.priority.band,
            "priority_breakdown": to_jsonb(assessment.priority.breakdown()),
            "impact_summary": to_jsonb({**impact.summary(), "impact_score": self.model.impact_score(impact)[0]}),
        }

    def _create(self, cur: psycopg.Cursor, outcome: RuleOutcome, assessment: IssueAssessment, rule_id: int,
                run_id: int, as_of: datetime) -> None:
        fields = self._fields(outcome, assessment)
        cur.execute("SELECT nextval(pg_get_serial_sequence('issues', 'issue_id')) AS issue_id")
        issue_id = cur.fetchone()["issue_id"]
        cur.execute(
            """
            INSERT INTO issues (issue_id, issue_key, rule_id, asset_id, owner_team_id, title, description, status,
                severity, business_criticality, affected_records, affected_rate, records_scanned, sample_failures,
                critical_columns, priority_score, priority_band, priority_breakdown, impact_summary,
                first_run_id, last_run_id, first_detected_at, last_detected_at)
            SELECT %(issue_id)s, %(issue_key)s, %(rule_id)s, a.asset_id, a.owner_team_id, %(title)s, %(description)s,
                'open', %(severity)s, a.criticality, %(affected_records)s, %(affected_rate)s, %(records_scanned)s,
                %(sample_failures)s, %(critical_columns)s, %(priority_score)s, %(priority_band)s,
                %(priority_breakdown)s, %(impact_summary)s, %(run_id)s, %(run_id)s, %(as_of)s, %(as_of)s
            FROM quality_rules r JOIN assets a ON a.asset_id = r.asset_id WHERE r.rule_id = %(rule_id)s
            """,
            {**fields, "issue_id": issue_id, "issue_key": f"DQ-{issue_id:04d}", "rule_id": rule_id,
             "run_id": run_id, "as_of": as_of},
        )
        self._replace_impacts(cur, issue_id, assessment.impact)
        self._event(cur, issue_id, "created", None, "open", "datatrust",
                    f"Detected {outcome.records_failed:,} failing rows", run_id)

    def _refresh(self, cur: psycopg.Cursor, current: dict, outcome: RuleOutcome, assessment: IssueAssessment,
                 run_id: int, as_of: datetime, log_event: bool = True) -> None:
        fields = self._fields(outcome, assessment)
        cur.execute(
            """
            UPDATE issues SET title = %(title)s, description = %(description)s, severity = %(severity)s,
                affected_records = %(affected_records)s, affected_rate = %(affected_rate)s,
                records_scanned = %(records_scanned)s, sample_failures = %(sample_failures)s,
                critical_columns = %(critical_columns)s, priority_score = %(priority_score)s,
                priority_band = %(priority_band)s, priority_breakdown = %(priority_breakdown)s,
                impact_summary = %(impact_summary)s, last_run_id = %(run_id)s, last_detected_at = %(as_of)s,
                occurrences = occurrences + 1, resolved_at = NULL, updated_at = now()
            WHERE issue_id = %(issue_id)s
            """,
            {**fields, "run_id": run_id, "as_of": as_of, "issue_id": current["issue_id"]},
        )
        self._replace_impacts(cur, current["issue_id"], assessment.impact)
        if log_event and current["affected_records"] != outcome.records_failed:
            self._event(cur, current["issue_id"], "redetected", current["status"], current["status"], "datatrust",
                        f"Affected rows changed {current['affected_records']:,} -> {outcome.records_failed:,}", run_id)

    @staticmethod
    def _replace_impacts(cur: psycopg.Cursor, issue_id: int, impact: ImpactReport) -> None:
        cur.execute("DELETE FROM issue_impacts WHERE issue_id = %s", (issue_id,))
        cur.executemany(
            """INSERT INTO issue_impacts (issue_id, impacted_asset_id, depth, is_critical, path)
               SELECT %s, asset_id, %s, %s, %s FROM assets WHERE name = %s""",
            [(issue_id, a.depth, a.is_critical, a.path, a.name) for a in impact.impacted],
        )

    @staticmethod
    def _event(cur: psycopg.Cursor, issue_id: int, event_type: str, from_status: str | None, to_status: str | None,
               actor: str, note: str | None, run_id: int | None = None) -> None:
        cur.execute(
            """INSERT INTO issue_events (issue_id, event_type, from_status, to_status, actor, note, run_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (issue_id, event_type, from_status, to_status, actor, note, run_id),
        )

    def _transition(self, cur: psycopg.Cursor, issue_id: int, from_status: str, to_status: str, actor: str,
                    note: str | None, run_id: int | None, event_type: str, resolved_at: datetime | None) -> None:
        cur.execute(
            "UPDATE issues SET status = %s, resolved_at = %s, updated_at = now() WHERE issue_id = %s",
            (to_status, resolved_at if to_status == "resolved" else None, issue_id),
        )
        self._event(cur, issue_id, event_type, from_status, to_status, actor, note, run_id)


def change_issue_status(db, issue_key: str, new_status: str, actor: str, note: str | None = None) -> None:
    """Validated manual status change from the UI or CLI."""
    if new_status not in TRANSITIONS:
        raise DataTrustError(f"Unknown status '{new_status}'")
    actor = (actor or "").strip()[:80]
    if not actor:
        raise DataTrustError("An actor name is required to change an issue's status")
    with db.transaction() as cur:
        cur.execute("SELECT issue_id, status FROM issues WHERE issue_key = %s FOR UPDATE", (issue_key,))
        issue = cur.fetchone()
        if issue is None:
            raise DataTrustError(f"Issue {issue_key} not found")
        if new_status not in TRANSITIONS[issue["status"]]:
            raise DataTrustError(f"Cannot move {issue_key} from {issue['status']} to {new_status}")
        if new_status in UNRESOLVED:
            cur.execute("SELECT 1 FROM issues WHERE rule_id = (SELECT rule_id FROM issues WHERE issue_id = %s) "
                        "AND status = ANY(%s) AND issue_id <> %s", (issue["issue_id"], list(UNRESOLVED), issue["issue_id"]))
            if cur.fetchone():
                raise DataTrustError("Another unresolved issue already tracks this rule")
        cur.execute(
            "UPDATE issues SET status = %s, resolved_at = CASE WHEN %s = 'resolved' THEN now()::timestamp END, "
            "updated_at = now() WHERE issue_id = %s",
            (new_status, new_status, issue["issue_id"]),
        )
        IssueManager._event(cur, issue["issue_id"], "status_change", issue["status"], new_status, actor,
                            (note or "").strip()[:2000] or None)


def add_issue_comment(db, issue_key: str, actor: str, note: str) -> None:
    actor, note = (actor or "").strip()[:80], (note or "").strip()[:2000]
    if not actor or not note:
        raise DataTrustError("Both an actor and a comment are required")
    with db.transaction() as cur:
        cur.execute("SELECT issue_id FROM issues WHERE issue_key = %s", (issue_key,))
        issue = cur.fetchone()
        if issue is None:
            raise DataTrustError(f"Issue {issue_key} not found")
        IssueManager._event(cur, issue["issue_id"], "comment", None, None, actor, note)

