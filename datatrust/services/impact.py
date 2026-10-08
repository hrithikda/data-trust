"""Impact analysis and what-if prioritisation for the UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from datatrust.config import Settings
from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.impact.analysis import ImpactAnalyzer, ImpactReport, MetadataContext
from datatrust.priority.scoring import IssueSignals, PriorityModel, PriorityResult
from datatrust.services.lineage import to_dot


@dataclass
class ImpactView:
    report: ImpactReport
    impact_score: float
    impact_level: str
    owners: list[dict[str, Any]]
    terms: list[dict[str, Any]]
    rules: list[dict[str, Any]]
    open_issues: list[dict[str, Any]]
    dot: str


class ImpactService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.model = PriorityModel.from_file(settings.config_dir / "priority_model.yml")
        self._context: MetadataContext | None = None

    @property
    def context(self) -> MetadataContext:
        if self._context is None:
            self.db.require_metadata("assets", "asset_dependencies", "glossary_terms")
            self._context = MetadataContext.load(self.db)
        return self._context

    def columns(self, asset: str) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT c.column_name, c.is_critical, c.cde_category FROM asset_columns c
               JOIN assets a ON a.asset_id = c.asset_id WHERE a.name = %s AND c.is_active
               ORDER BY c.ordinal_position""", (asset,))

    def analyze(self, asset: str, columns: list[str] | None = None, row_level: bool = False) -> ImpactView:
        analyzer = ImpactAnalyzer(self.context)
        report = analyzer.analyze(asset, columns or None, row_level=row_level)
        score, level = self.model.impact_score(report)
        team_names = report.teams_affected
        owners = self.db.fetch_all(
            """SELECT t.name, t.lead_name, t.email, t.slack_channel,
                      (SELECT array_agg(a.name ORDER BY a.name) FROM assets a
                       WHERE a.owner_team_id = t.team_id AND a.name = ANY(%(assets)s)) AS owned_assets
               FROM teams t WHERE t.name = ANY(%(teams)s)""",
            {"teams": team_names, "assets": [asset] + [i.name for i in report.impacted]})
        owners.sort(key=lambda o: team_names.index(o["name"]))
        terms = self.db.fetch_all(
            "SELECT name, definition, domain, status FROM glossary_terms WHERE name = ANY(%s) ORDER BY name",
            (report.glossary_terms,))
        rules = self.db.fetch_all(
            """SELECT r.rule_key, r.name, r.category, r.severity, r.column_names FROM quality_rules r
               JOIN assets a ON a.asset_id = r.asset_id WHERE a.name = %s AND r.is_active ORDER BY r.rule_key""",
            (asset,))
        downstream = [i.name for i in report.impacted]
        open_issues = self.db.fetch_all(
            """SELECT i.issue_key, i.title, i.priority_band, i.priority_score::float AS priority_score, a.name AS asset
               FROM issues i JOIN assets a ON a.asset_id = i.asset_id
               WHERE i.status <> 'resolved' AND a.name = ANY(%s) ORDER BY i.priority_score DESC""",
            ([asset] + self.context.graph.all_upstream(asset),))
        graph = self.context.graph
        keep = {asset, *downstream, *graph.direct_upstream(asset)}
        return ImpactView(report=report, impact_score=score, impact_level=level, owners=owners, terms=terms,
                          rules=rules, open_issues=open_issues,
                          dot=to_dot(graph.subgraph(keep), focus=asset, impacted=downstream))

    def what_if(self, asset: str, columns: list[str], severity: str, records_failed: int, records_scanned: int,
                row_level: bool = False) -> PriorityResult:
        """Priority a hypothetical defect would receive; uses the same model as real issues."""
        if records_scanned <= 0 or not 0 <= records_failed <= records_scanned:
            raise DataTrustError("records_failed must be between 0 and records_scanned (> 0)")
        criticality = self.db.fetch_value("SELECT criticality FROM assets WHERE name = %s", (asset,))
        if criticality is None:
            raise DataTrustError(f"Unknown asset '{asset}'")
        cdes = [(r["column_name"], r["cde_category"]) for r in self.columns(asset)
                if r["is_critical"] and r["column_name"] in columns]
        report = ImpactAnalyzer(self.context).analyze(asset, columns or None, row_level=row_level)
        return self.model.score(IssueSignals(severity, records_failed, records_scanned, criticality, cdes), report)
