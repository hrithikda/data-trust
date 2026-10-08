"""Directed lineage graph over warehouse assets.

Nodes are assets (sources, models, exposures); an edge ``A -> B`` means B reads from A.
Edges optionally carry the upstream columns B references, which lets impact analysis
prune propagation for defects confined to columns nobody downstream reads.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

import networkx as nx

from datatrust.errors import DataTrustError


class UnknownAssetError(DataTrustError):
    """The requested asset is not part of the lineage graph."""


@dataclass(frozen=True)
class AssetNode:
    name: str
    asset_type: str
    layer: str
    criticality: str = "medium"
    domain: str = "unknown"
    owner_team: str | None = None
    audience: str | None = None
    is_financial_reporting: bool = False
    is_customer_facing: bool = False
    source_system: str | None = None

    @property
    def is_critical(self) -> bool:
        return self.criticality in {"high", "critical"}

    @property
    def is_executive(self) -> bool:
        return self.audience == "executive" or self.domain == "executive"


@dataclass(frozen=True)
class Edge:
    upstream: str
    downstream: str
    referenced_columns: tuple[str, ...] | None = None


class LineageGraph:
    """Immutable lineage graph with traversal helpers."""

    def __init__(self, nodes: Iterable[AssetNode], edges: Iterable[Edge]) -> None:
        self._graph = nx.DiGraph()
        for node in nodes:
            self._graph.add_node(node.name, node=node)
        for edge in edges:
            if edge.upstream not in self._graph or edge.downstream not in self._graph:
                continue
            self._graph.add_edge(edge.upstream, edge.downstream, referenced_columns=edge.referenced_columns)
        if not nx.is_directed_acyclic_graph(self._graph):
            raise DataTrustError("Lineage contains a cycle; dbt metadata is inconsistent")

    # ------------------------------------------------------------- loading
    @classmethod
    def from_database(cls, db) -> LineageGraph:
        rows = db.fetch_all(
            """
            SELECT a.name, a.asset_type, a.layer, a.criticality, a.domain, t.name AS owner_team, a.audience,
                   a.is_financial_reporting, a.is_customer_facing, ss.name AS source_system
            FROM assets a
            LEFT JOIN teams t ON t.team_id = a.owner_team_id
            LEFT JOIN source_mappings sm ON sm.asset_id = a.asset_id
            LEFT JOIN source_systems ss ON ss.source_system_id = sm.source_system_id
            WHERE a.is_active
            """
        )
        edges = db.fetch_all(
            """
            SELECT u.name AS upstream, d.name AS downstream, dep.referenced_columns
            FROM asset_dependencies dep
            JOIN assets u ON u.asset_id = dep.upstream_asset_id AND u.is_active
            JOIN assets d ON d.asset_id = dep.downstream_asset_id AND d.is_active
            """
        )
        return cls(
            (AssetNode(**row) for row in rows),
            (Edge(e["upstream"], e["downstream"],
                  tuple(e["referenced_columns"]) if e["referenced_columns"] is not None else None) for e in edges),
        )

    # ------------------------------------------------------------- basics
    def __contains__(self, name: str) -> bool:
        return name in self._graph

    def __len__(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def node(self, name: str) -> AssetNode:
        self._require(name)
        return self._graph.nodes[name]["node"]

    def nodes(self) -> list[AssetNode]:
        return [data["node"] for _, data in self._graph.nodes(data=True)]

    def edges(self) -> list[Edge]:
        return [Edge(u, v, data.get("referenced_columns")) for u, v, data in self._graph.edges(data=True)]

    def _require(self, name: str) -> None:
        if name not in self._graph:
            raise UnknownAssetError(f"Asset '{name}' is not in the lineage graph")

    # ------------------------------------------------------------- traversal
    def direct_upstream(self, name: str) -> list[str]:
        self._require(name)
        return sorted(self._graph.predecessors(name))

    def direct_downstream(self, name: str) -> list[str]:
        self._require(name)
        return sorted(self._graph.successors(name))

    def all_upstream(self, name: str) -> list[str]:
        self._require(name)
        return sorted(nx.ancestors(self._graph, name))

    def all_downstream(self, name: str) -> list[str]:
        self._require(name)
        return sorted(nx.descendants(self._graph, name))

    def upstream_depths(self, name: str) -> dict[str, int]:
        """Ancestor -> minimum number of hops to ``name``."""
        self._require(name)
        lengths = nx.single_source_shortest_path_length(self._graph.reverse(copy=False), name)
        return {k: v for k, v in lengths.items() if k != name}

    def downstream_depths(self, name: str, columns: Iterable[str] | None = None) -> dict[str, int]:
        """Descendant -> minimum hops from ``name``.

        When ``columns`` is given, the first hop only follows edges whose consumer references
        at least one of those columns (unknown references count as referenced). Beyond the
        first hop the defect is assumed to travel with the derived rows.
        """
        self._require(name)
        tracked = set(columns or [])
        depths: dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        for child in self._graph.successors(name):
            referenced = self._graph.edges[name, child].get("referenced_columns")
            if not tracked or referenced is None or tracked & set(referenced):
                depths[child] = 1
                queue.append((child, 1))
        while queue:
            current, depth = queue.popleft()
            for child in self._graph.successors(current):
                if child not in depths:
                    depths[child] = depth + 1
                    queue.append((child, depth + 1))
        return depths

    def unreferenced_consumers(self, name: str, columns: Iterable[str]) -> list[str]:
        """Direct consumers whose SQL does not reference any of ``columns``."""
        self._require(name)
        tracked = set(columns)
        return sorted(
            child for child in self._graph.successors(name)
            if (refs := self._graph.edges[name, child].get("referenced_columns")) is not None
            and not tracked & set(refs)
        )

    def shortest_path(self, source: str, target: str) -> list[str] | None:
        self._require(source)
        self._require(target)
        try:
            return nx.shortest_path(self._graph, source, target)
        except nx.NetworkXNoPath:
            return None

    def all_paths(self, source: str, target: str, limit: int = 20) -> list[list[str]]:
        """Up to ``limit`` simple paths, shortest first."""
        self._require(source)
        self._require(target)
        if source == target or not nx.has_path(self._graph, source, target):
            return []
        paths = []
        for path in nx.shortest_simple_paths(self._graph, source, target):
            paths.append(path)
            if len(paths) >= limit:
                break
        return paths

    def neighbourhood(self, name: str, upstream_depth: int = 2, downstream_depth: int = 2) -> LineageGraph:
        """Sub-graph within the given number of hops of ``name`` (both directions)."""
        keep = {name}
        keep |= {n for n, d in self.upstream_depths(name).items() if d <= upstream_depth}
        keep |= {n for n, d in self.downstream_depths(name).items() if d <= downstream_depth}
        return self.subgraph(keep)

    def subgraph(self, names: Iterable[str]) -> LineageGraph:
        keep = set(names) & set(self._graph.nodes)
        return LineageGraph((self.node(n) for n in keep),
                            (e for e in self.edges() if e.upstream in keep and e.downstream in keep))

    def longest_chain(self) -> list[str]:
        """Deepest dependency chain in the platform (useful as a lineage depth metric)."""
        return nx.dag_longest_path(self._graph)
