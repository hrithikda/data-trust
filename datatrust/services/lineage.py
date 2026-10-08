"""Lineage exploration and Graphviz rendering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.lineage.graph import LineageGraph

LAYER_ORDER = ("raw", "staging", "intermediate", "mart", "exposure")
LAYER_STYLE = {
    "raw": ("#eef2f7", "#7b8794"),
    "staging": ("#e3f0ff", "#3d7bd9"),
    "intermediate": ("#ede7fb", "#7a5cc7"),
    "mart": ("#e1f5ea", "#2f9e63"),
    "exposure": ("#fff3dc", "#d08a12"),
}
IMPACT_FILL = "#fde2e1"
IMPACT_BORDER = "#d64545"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def to_dot(graph: LineageGraph, focus: str | None = None, impacted: Iterable[str] = (),
           highlight_path: Iterable[str] = (), rankdir: str = "LR") -> str:
    """Graphviz DOT for a lineage (sub)graph, clustered by warehouse layer.

    ``impacted`` nodes are drawn in red (blast radius); ``highlight_path`` edges are thickened.
    """
    impacted_set, path = set(impacted), list(highlight_path)
    path_edges = set(zip(path, path[1:], strict=False))
    lines = [
        "digraph lineage {",
        f'  rankdir={rankdir}; nodesep=0.12; ranksep=0.45; bgcolor="transparent";',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=14, penwidth=1.2, '
        'margin="0.12,0.04"];',
        '  edge [color="#9aa5b1", arrowsize=0.6];',
    ]
    by_layer: dict[str, list[str]] = {}
    for node in graph.nodes():
        by_layer.setdefault(node.layer, []).append(node.name)
    for layer in LAYER_ORDER:
        names = sorted(by_layer.get(layer, []))
        if not names:
            continue
        fill, border = LAYER_STYLE[layer]
        lines.append(f"  subgraph cluster_{layer} {{")
        lines.append(f'    label={_quote(layer.title())}; fontname="Helvetica"; fontsize=15; fontcolor="#52606d";')
        lines.append('    style="rounded,dashed"; color="#cbd2d9";')
        for name in names:
            node = graph.node(name)
            node_fill, node_border = fill, border
            width = 2.2 if node.criticality == "critical" else 1.2
            if name in impacted_set:
                node_fill, node_border = IMPACT_FILL, IMPACT_BORDER
            if name == focus:
                node_border, width = "#1f2933", 3.0
            shape = ', shape=note' if node.asset_type == "exposure" else (
                ', shape=cylinder' if node.asset_type == "source" else "")
            weight = ', fontname="Helvetica-Bold"' if node.criticality == "critical" else ""
            lines.append(f'    {_quote(name)} [label={_quote(name)}, fillcolor="{node_fill}", '
                         f'color="{node_border}", penwidth={width}{shape}{weight}];')
        lines.append("  }")
    for edge in graph.edges():
        if (edge.upstream, edge.downstream) in path_edges:
            attrs = ' [color="#1f2933", penwidth=2.4]'
        elif edge.upstream in impacted_set | {focus} and edge.downstream in impacted_set:
            attrs = f' [color="{IMPACT_BORDER}"]'
        else:
            attrs = ""
        lines.append(f"  {_quote(edge.upstream)} -> {_quote(edge.downstream)}{attrs};")
    lines.append("}")
    return "\n".join(lines)


@dataclass
class AssetLineage:
    asset: str
    direct_upstream: list[str]
    direct_downstream: list[str]
    upstream: list[dict[str, Any]]
    downstream: list[dict[str, Any]]
    dot: str


class LineageService:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._graph: LineageGraph | None = None

    @property
    def graph(self) -> LineageGraph:
        if self._graph is None:
            self.db.require_metadata("assets", "asset_dependencies")
            graph = LineageGraph.from_database(self.db)
            if not len(graph):
                raise DataTrustError("The lineage graph is empty. Run `datatrust ingest` after `datatrust dbt`.")
            self._graph = graph
        return self._graph

    def stats(self) -> dict[str, Any]:
        chain = self.graph.longest_chain()
        counts: dict[str, int] = {}
        for node in self.graph.nodes():
            counts[node.layer] = counts.get(node.layer, 0) + 1
        return {"assets": len(self.graph), "edges": self.graph.edge_count, "longest_chain": chain,
                "max_depth": len(chain) - 1, "by_layer": counts}

    def _rows(self, depths: dict[str, int]) -> list[dict[str, Any]]:
        rows = []
        for name, depth in sorted(depths.items(), key=lambda x: (x[1], x[0])):
            node = self.graph.node(name)
            rows.append({"asset": name, "depth": depth, "layer": node.layer, "type": node.asset_type,
                         "criticality": node.criticality, "owner": node.owner_team})
        return rows

    def asset_lineage(self, asset: str, upstream_depth: int = 2, downstream_depth: int = 2) -> AssetLineage:
        g = self.graph
        sub = g.neighbourhood(asset, upstream_depth, downstream_depth)
        return AssetLineage(
            asset=asset, direct_upstream=g.direct_upstream(asset), direct_downstream=g.direct_downstream(asset),
            upstream=self._rows(g.upstream_depths(asset)), downstream=self._rows(g.downstream_depths(asset)),
            dot=to_dot(sub, focus=asset),
        )

    def paths(self, source: str, target: str, limit: int = 10) -> dict[str, Any]:
        g = self.graph
        paths = g.all_paths(source, target, limit)
        nodes = {n for p in paths for n in p} or {source, target}
        return {"paths": paths, "dot": to_dot(g.subgraph(nodes), focus=source,
                                              highlight_path=paths[0] if paths else ())}

    def full_dot(self) -> str:
        return to_dot(self.graph)
