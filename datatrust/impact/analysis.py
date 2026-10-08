"""What breaks if this asset (or these columns) becomes incorrect?

Impact analysis walks the lineage graph downstream and enriches every reached asset with
ownership, criticality, business consumers and glossary terms. For column-scoped defects the
first hop is pruned to consumers whose SQL references the affected columns; row-level defects
(duplicates, orphans that fan out joins) propagate to every consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from datatrust.lineage.graph import LineageGraph


@dataclass
class MetadataContext:
    """Everything impact analysis needs, loadable from the DB or built in memory for tests."""

    graph: LineageGraph
    asset_terms: dict[str, set[str]] = field(default_factory=dict)            # asset -> terms linked at asset level
    column_terms: dict[tuple[str, str], set[str]] = field(default_factory=dict)  # (asset, column) -> terms
    asset_rules: dict[str, list[str]] = field(default_factory=dict)            # asset -> active rule keys

    @classmethod
    def load(cls, db) -> MetadataContext:
        context = cls(graph=LineageGraph.from_database(db))
        for row in db.fetch_all(
            """
            SELECT a.name AS asset, c.column_name, g.name AS term
            FROM glossary_term_links l
            JOIN glossary_terms g ON g.term_id = l.term_id
            JOIN assets a ON a.asset_id = l.asset_id
            LEFT JOIN asset_columns c ON c.column_id = l.column_id
            WHERE g.status <> 'deprecated'
            """
        ):
            if row["column_name"]:
                context.column_terms.setdefault((row["asset"], row["column_name"]), set()).add(row["term"])
            else:
                context.asset_terms.setdefault(row["asset"], set()).add(row["term"])
        for row in db.fetch_all(
            "SELECT a.name AS asset, r.rule_key FROM quality_rules r JOIN assets a ON a.asset_id = r.asset_id "
            "WHERE r.is_active ORDER BY r.rule_key"
        ):
            context.asset_rules.setdefault(row["asset"], []).append(row["rule_key"])
        return context

    def terms_for(self, asset: str, columns: list[str] | None = None) -> set[str]:
        terms = set(self.asset_terms.get(asset, set()))
        for (term_asset, column), names in self.column_terms.items():
            if term_asset == asset and (not columns or column in columns):
                terms |= names
        return terms


@dataclass
class ImpactedAsset:
    name: str
    asset_type: str
    layer: str
    depth: int
    criticality: str
    owner_team: str | None
    is_critical: bool
    is_financial_reporting: bool
    is_customer_facing: bool
    is_executive: bool
    path: list[str]


@dataclass
class ImpactReport:
    asset: str
    columns: list[str]
    row_level: bool
    owner_team: str | None
    criticality: str
    direct_consumers: list[str]
    impacted: list[ImpactedAsset]
    unaffected_consumers: list[str]
    glossary_terms: list[str]
    related_rules: list[str]
    upstream_sources: list[str]

    @property
    def downstream_count(self) -> int:
        return len(self.impacted)

    @property
    def critical_assets(self) -> list[ImpactedAsset]:
        return [a for a in self.impacted if a.is_critical]

    @property
    def marts(self) -> list[ImpactedAsset]:
        return [a for a in self.impacted if a.layer == "mart"]

    @property
    def exposures(self) -> list[ImpactedAsset]:
        return [a for a in self.impacted if a.asset_type == "exposure"]

    @property
    def financial_reporting_affected(self) -> bool:
        return any(a.is_financial_reporting for a in self.impacted)

    @property
    def customer_facing_affected(self) -> bool:
        return any(a.is_customer_facing for a in self.impacted)

    @property
    def executive_affected(self) -> bool:
        return any(a.is_executive for a in self.impacted)

    @property
    def hops_to_executive(self) -> int | None:
        depths = [a.depth for a in self.impacted if a.is_executive]
        return min(depths) if depths else None

    @property
    def max_depth(self) -> int:
        return max((a.depth for a in self.impacted), default=0)

    @property
    def teams_affected(self) -> list[str]:
        """Owner of the broken asset first, then every team owning an impacted asset."""
        teams = [self.owner_team] if self.owner_team else []
        teams += sorted({a.owner_team for a in self.impacted if a.owner_team} - set(teams))
        return teams

    def summary(self) -> dict:
        """Compact, JSON-serialisable view stored on issues."""
        return {
            "asset": self.asset,
            "columns": self.columns,
            "row_level": self.row_level,
            "downstream_count": self.downstream_count,
            "direct_consumers": self.direct_consumers,
            "unaffected_consumers": self.unaffected_consumers,
            "critical_assets": [a.name for a in self.critical_assets],
            "marts": [a.name for a in self.marts],
            "exposures": [a.name for a in self.exposures],
            "teams_affected": self.teams_affected,
            "glossary_terms": self.glossary_terms,
            "financial_reporting_affected": self.financial_reporting_affected,
            "customer_facing_affected": self.customer_facing_affected,
            "executive_affected": self.executive_affected,
            "hops_to_executive": self.hops_to_executive,
            "max_depth": self.max_depth,
            "upstream_sources": self.upstream_sources,
        }


class ImpactAnalyzer:
    def __init__(self, context: MetadataContext) -> None:
        self.context = context
        self.graph = context.graph

    def analyze(self, asset: str, columns: list[str] | None = None, row_level: bool = False) -> ImpactReport:
        """Blast radius of a defect in ``asset`` (optionally confined to ``columns``)."""
        node = self.graph.node(asset)
        tracked = [] if row_level else list(columns or [])
        depths = self.graph.downstream_depths(asset, tracked or None)
        reachable = self.graph.subgraph(set(depths) | {asset})  # paths must stay inside the pruned blast radius
        impacted: list[ImpactedAsset] = []
        for name, depth in sorted(depths.items(), key=lambda item: (item[1], item[0])):
            target = self.graph.node(name)
            impacted.append(ImpactedAsset(
                name=name, asset_type=target.asset_type, layer=target.layer, depth=depth,
                criticality=target.criticality, owner_team=target.owner_team, is_critical=target.is_critical,
                is_financial_reporting=target.is_financial_reporting, is_customer_facing=target.is_customer_facing,
                is_executive=target.is_executive, path=reachable.shortest_path(asset, name) or [asset, name],
            ))
        terms = self.context.terms_for(asset, tracked or None)
        for item in impacted:
            terms |= self.context.terms_for(item.name)
        sources = sorted({self.graph.node(n).source_system for n in self.graph.all_upstream(asset) + [asset]
                          if self.graph.node(n).source_system})
        return ImpactReport(
            asset=asset, columns=list(columns or []), row_level=row_level, owner_team=node.owner_team,
            criticality=node.criticality,
            direct_consumers=[i.name for i in impacted if i.depth == 1],
            impacted=impacted,
            unaffected_consumers=self.graph.unreferenced_consumers(asset, tracked) if tracked else [],
            glossary_terms=sorted(terms),
            related_rules=self.context.asset_rules.get(asset, []),
            upstream_sources=sources,
        )
