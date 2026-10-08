"""Catalog discovery and dataset detail.

Search runs in memory over a small denormalised index (assets with their columns, glossary
terms, owner, domain and source system). The warehouse has tens of assets and hundreds of
columns, so this is fast and lets every hit explain *why* it matched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.lineage.graph import LineageGraph

LATEST_RUN = "(SELECT run_id FROM quality_runs WHERE status = 'succeeded' ORDER BY as_of DESC, run_id DESC LIMIT 1)"
LATEST_PROFILE = ("(SELECT profile_run_id FROM profile_runs WHERE status = 'succeeded' "
                  "ORDER BY profile_run_id DESC LIMIT 1)")

# Points per field a query token can match; the highest-scoring field per token counts.
MATCH_WEIGHTS = {"name": 10, "column": 6, "glossary": 5, "owner": 4, "domain": 4, "source_system": 4,
                 "description": 2}


@dataclass
class AssetHit:
    name: str
    asset_type: str
    layer: str
    domain: str
    owner: str | None
    criticality: str
    description: str
    source_system: str | None
    column_count: int
    critical_fields: int
    health: float | None
    open_issues: int
    freshness_status: str | None
    row_count: int | None
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class _IndexEntry:
    hit: AssetHit
    columns: list[str]
    terms: list[str]


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"[\s,]+", query.lower().strip()) if t]


class CatalogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------------ index
    def _index(self) -> list[_IndexEntry]:
        self.db.require_metadata("assets", "asset_columns")
        rows = self.db.fetch_all(
            f"""
            SELECT a.name, a.asset_type, a.layer, a.domain, t.name AS owner, a.criticality, a.description,
                   ss.name AS source_system,
                   (SELECT count(*) FROM asset_columns c WHERE c.asset_id = a.asset_id AND c.is_active) AS column_count,
                   (SELECT count(*) FROM asset_columns c WHERE c.asset_id = a.asset_id AND c.is_active
                        AND c.is_critical) AS critical_fields,
                   (SELECT array_agg(c.column_name ORDER BY c.ordinal_position) FROM asset_columns c
                        WHERE c.asset_id = a.asset_id AND c.is_active) AS columns,
                   (SELECT array_agg(DISTINCT g.name) FROM glossary_term_links l JOIN glossary_terms g
                        ON g.term_id = l.term_id WHERE l.asset_id = a.asset_id) AS terms,
                   (SELECT round(100 * sum(qr.rule_score * qr.severity_weight)
                                 / nullif(sum(qr.severity_weight) FILTER (WHERE qr.rule_score IS NOT NULL), 0), 1)
                        FROM quality_results qr WHERE qr.asset_id = a.asset_id AND qr.run_id = {LATEST_RUN})::float
                        AS health,
                   (SELECT count(*) FROM issues i WHERE i.asset_id = a.asset_id
                        AND i.status IN ('open', 'investigating')) AS open_issues,
                   ap.freshness_status, ap.row_count
            FROM assets a
            LEFT JOIN teams t ON t.team_id = a.owner_team_id
            LEFT JOIN source_mappings sm ON sm.asset_id = a.asset_id
            LEFT JOIN source_systems ss ON ss.source_system_id = sm.source_system_id
            LEFT JOIN asset_profiles ap ON ap.asset_id = a.asset_id AND ap.profile_run_id = {LATEST_PROFILE}
            WHERE a.is_active
            ORDER BY a.name
            """
        )
        entries = []
        graph = LineageGraph.from_database(self.db)
        for r in rows:
            source_system = r["source_system"]
            if source_system is None and r["name"] in graph:
                systems = sorted({graph.node(n).source_system for n in graph.all_upstream(r["name"])
                                  if graph.node(n).source_system})
                source_system = ", ".join(systems) or None
            hit = AssetHit(
                name=r["name"], asset_type=r["asset_type"], layer=r["layer"], domain=r["domain"], owner=r["owner"],
                criticality=r["criticality"], description=r["description"], source_system=source_system,
                column_count=int(r["column_count"]), critical_fields=int(r["critical_fields"]), health=r["health"],
                open_issues=int(r["open_issues"]), freshness_status=r["freshness_status"],
                row_count=int(r["row_count"]) if r["row_count"] is not None else None,
            )
            entries.append(_IndexEntry(hit, list(r["columns"] or []), list(r["terms"] or [])))
        return entries

    # ------------------------------------------------------------------ search
    def search(self, query: str = "", layers: list[str] | None = None, domains: list[str] | None = None,
               owners: list[str] | None = None, criticalities: list[str] | None = None,
               only_with_issues: bool = False) -> list[AssetHit]:
        """Every query token must match some field; hits are ranked by the best field per token."""
        tokens = _tokens(query)
        hits: list[AssetHit] = []
        for entry in self._index():
            hit = entry.hit
            if layers and hit.layer not in layers:
                continue
            if domains and hit.domain not in domains:
                continue
            if owners and hit.owner not in owners:
                continue
            if criticalities and hit.criticality not in criticalities:
                continue
            if only_with_issues and not hit.open_issues:
                continue
            score, reasons = self._match(entry, tokens)
            if tokens and score is None:
                continue
            hit.score, hit.reasons = score or 0.0, reasons
            hits.append(hit)
        criticality_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
        return sorted(hits, key=lambda h: (-h.score, -criticality_rank.get(h.criticality, 0), h.name))

    @staticmethod
    def _match(entry: _IndexEntry, tokens: list[str]) -> tuple[float | None, list[str]]:
        hit = entry.hit
        fields: dict[str, list[str]] = {
            "name": [hit.name], "column": entry.columns, "glossary": entry.terms, "owner": [hit.owner or ""],
            "domain": [hit.domain], "source_system": [hit.source_system or ""], "description": [hit.description],
        }
        total, reasons = 0.0, []
        for token in tokens:
            best: tuple[int, str] | None = None
            for kind, values in fields.items():
                matched = [v for v in values if token in v.lower()]
                if matched and (best is None or MATCH_WEIGHTS[kind] > best[0]):
                    label = kind.replace("_", " ")
                    detail = ", ".join(matched[:3]) if kind in ("column", "glossary") else ""
                    best = (MATCH_WEIGHTS[kind], f"{label}: {detail}" if detail else f"{label} matches '{token}'")
            if best is None:
                return None, []
            exact = token == hit.name.lower()
            total += best[0] * (2 if exact else 1)
            if best[1] not in reasons:
                reasons.append(best[1])
        return total, reasons

    def filter_options(self) -> dict[str, list[str]]:
        rows = self.db.fetch_all(
            "SELECT DISTINCT a.layer, a.domain, t.name AS owner, a.criticality FROM assets a "
            "LEFT JOIN teams t ON t.team_id = a.owner_team_id WHERE a.is_active")
        return {key: sorted({r[key] for r in rows if r[key]}) for key in ("layer", "domain", "owner", "criticality")}

    def asset_names(self) -> list[str]:
        return [r["name"] for r in self.db.fetch_all("SELECT name FROM assets WHERE is_active ORDER BY name")]

    # ------------------------------------------------------------------ detail
    def asset_detail(self, name: str) -> dict[str, Any]:
        asset = self.db.fetch_one(
            """SELECT a.*, t.name AS owner, t.team_key, t.lead_name, t.email AS owner_email,
                      t.slack_channel, t.on_call_rotation,
                      ss.name AS source_system, ss.system_type, ss.vendor, ss.ingestion_method, ss.load_frequency,
                      sm.source_object, sm.extraction_notes
               FROM assets a
               LEFT JOIN teams t ON t.team_id = a.owner_team_id
               LEFT JOIN source_mappings sm ON sm.asset_id = a.asset_id
               LEFT JOIN source_systems ss ON ss.source_system_id = sm.source_system_id
               WHERE a.name = %s AND a.is_active""",
            (name,),
        )
        if asset is None:
            raise DataTrustError(f"Asset '{name}' is not in the catalog")
        asset_id = asset["asset_id"]
        graph = LineageGraph.from_database(self.db)
        upstream_depths = graph.upstream_depths(name)
        downstream_depths = graph.downstream_depths(name)
        lineage_sources = sorted(
            {(graph.node(n).source_system, n) for n in upstream_depths if graph.node(n).source_system})
        return {
            "asset": asset,
            "columns": self._columns(asset_id),
            "terms": self._terms(asset_id),
            "profile": self.db.fetch_one(
                """SELECT ap.*, pr.started_at AS profiled_run_at FROM asset_profiles ap
                   JOIN profile_runs pr ON pr.profile_run_id = ap.profile_run_id
                   WHERE ap.asset_id = %s ORDER BY ap.profile_run_id DESC LIMIT 1""", (asset_id,)),
            "upstream": [{"name": n, "depth": d, "layer": graph.node(n).layer, "type": graph.node(n).asset_type}
                         for n, d in sorted(upstream_depths.items(), key=lambda x: (x[1], x[0]))],
            "downstream": [{"name": n, "depth": d, "layer": graph.node(n).layer, "type": graph.node(n).asset_type,
                            "criticality": graph.node(n).criticality, "owner": graph.node(n).owner_team}
                           for n, d in sorted(downstream_depths.items(), key=lambda x: (x[1], x[0]))],
            "origin_systems": [{"source_system": s, "raw_table": t} for s, t in lineage_sources],
            "rules": self._rules(asset_id),
            "issues": self.db.fetch_all(
                """SELECT issue_key, title, status, severity, priority_band, priority_score::float AS priority_score,
                          affected_records FROM issues WHERE asset_id = %s AND status <> 'resolved'
                   ORDER BY priority_score DESC""", (asset_id,)),
            "inherited_issues": self.db.fetch_all(
                """SELECT i.issue_key, i.title, i.status, i.priority_band, i.priority_score::float AS priority_score,
                          src.name AS source_asset, ii.depth, ii.path
                   FROM issue_impacts ii JOIN issues i ON i.issue_id = ii.issue_id
                   JOIN assets src ON src.asset_id = i.asset_id
                   WHERE ii.impacted_asset_id = %s AND i.status <> 'resolved'
                   ORDER BY i.priority_score DESC""", (asset_id,)),
            "dbt_tests": self.db.fetch_all(
                """SELECT test_name, column_name, severity, last_status, last_failures FROM dbt_tests
                   WHERE asset_id = %s ORDER BY column_name NULLS FIRST, test_name""", (asset_id,)),
            "health": self.db.fetch_value(
                f"""SELECT round(100 * sum(rule_score * severity_weight)
                                 / nullif(sum(severity_weight) FILTER (WHERE rule_score IS NOT NULL), 0), 1)::float
                    FROM quality_results WHERE asset_id = %s AND run_id = {LATEST_RUN}""", (asset_id,)),
        }

    def _columns(self, asset_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            f"""
            SELECT c.column_name, c.data_type, c.description, c.is_critical, c.cde_category, c.critical_reason,
                   c.contains_pii,
                   (SELECT array_agg(g.name ORDER BY g.name) FROM glossary_term_links l
                        JOIN glossary_terms g ON g.term_id = l.term_id WHERE l.column_id = c.column_id) AS terms,
                   cp.null_rate::float AS null_rate, cp.distinct_count, cp.uniqueness::float AS uniqueness,
                   cp.min_value, cp.max_value
            FROM asset_columns c
            LEFT JOIN asset_profiles ap ON ap.asset_id = c.asset_id AND ap.profile_run_id = {LATEST_PROFILE}
            LEFT JOIN column_profiles cp ON cp.asset_profile_id = ap.asset_profile_id AND cp.column_id = c.column_id
            WHERE c.asset_id = %s AND c.is_active
            ORDER BY c.ordinal_position
            """,
            (asset_id,),
        )

    def _terms(self, asset_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            """SELECT g.term_key, g.name, g.definition, g.status, array_remove(array_agg(DISTINCT c.column_name), NULL)
                      AS columns
               FROM glossary_term_links l JOIN glossary_terms g ON g.term_id = l.term_id
               LEFT JOIN asset_columns c ON c.column_id = l.column_id
               WHERE l.asset_id = %s GROUP BY g.term_key, g.name, g.definition, g.status ORDER BY g.name""",
            (asset_id,),
        )

    def _rules(self, asset_id: int) -> list[dict[str, Any]]:
        return self.db.fetch_all(
            f"""SELECT r.rule_key, r.name, r.category, r.severity, r.column_names, r.rulesets,
                       qr.status, qr.records_failed, qr.failure_rate::float AS failure_rate
                FROM quality_rules r
                LEFT JOIN quality_results qr ON qr.rule_id = r.rule_id AND qr.run_id = {LATEST_RUN}
                WHERE r.asset_id = %s AND r.is_active
                ORDER BY (qr.status = 'fail') DESC NULLS LAST,
                         array_position(ARRAY['critical', 'high', 'medium', 'low'], r.severity), r.rule_key""",
            (asset_id,),
        )
