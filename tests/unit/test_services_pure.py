"""Service logic that does not need a database: search ranking and lineage rendering."""

from datatrust.services.catalog import AssetHit, CatalogService, _IndexEntry
from datatrust.services.lineage import to_dot
from tests.factories import graph


def entry(name: str, columns=(), terms=(), owner="Commerce", domain="commerce", description="",
          source_system=None) -> _IndexEntry:
    hit = AssetHit(name=name, asset_type="model", layer="staging", domain=domain, owner=owner, criticality="high",
                   description=description, source_system=source_system, column_count=len(columns),
                   critical_fields=0, health=None, open_issues=0, freshness_status=None, row_count=None)
    return _IndexEntry(hit, list(columns), list(terms))


def test_search_ranks_name_matches_above_column_and_description_matches():
    by_name = CatalogService._match(entry("fct_payments"), ["payment"])
    by_column = CatalogService._match(entry("fct_orders", columns=["payment_status"]), ["payment"])
    by_text = CatalogService._match(entry("fct_daily", description="Payment totals"), ["payment"])
    assert by_name[0] > by_column[0] > by_text[0]
    assert by_column[1] == ["column: payment_status"]


def test_every_token_must_match_and_reasons_explain_it():
    e = entry("stg_payrail__refunds", terms=["Refund"], owner="Payments", source_system="PayRail")
    score, reasons = CatalogService._match(e, ["refund", "payrail"])
    assert score == 20  # both tokens hit the name (10 each)
    assert CatalogService._match(e, ["refund", "subscription"]) == (None, [])
    score, reasons = CatalogService._match(entry("fct_orders", owner="Finance Analytics"), ["finance"])
    assert reasons == ["owner matches 'finance'"]


def test_exact_name_match_counts_double():
    assert CatalogService._match(entry("fct_orders"), ["fct_orders"])[0] == 20


def test_dot_rendering_clusters_layers_and_highlights_impact():
    dot = to_dot(graph(), focus="stg_orders", impacted=["fct_orders"],
                 highlight_path=["stg_orders", "fct_orders"])
    assert dot.startswith("digraph lineage {") and dot.endswith("}")
    for layer in ("raw", "staging", "intermediate", "mart", "exposure"):
        assert f"subgraph cluster_{layer}" in dot
    assert '"stg_orders" -> "fct_orders" [color="#1f2933", penwidth=2.4];' in dot
    assert '"fct_orders" [label="fct_orders", fillcolor="#fde2e1"' in dot
    assert '"executive_dashboard" [label="executive_dashboard"' in dot and "shape=note" in dot
    assert dot.count("->") == graph().edge_count
