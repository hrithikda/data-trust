import pytest

from datatrust.errors import DataTrustError
from datatrust.lineage.graph import AssetNode, Edge, LineageGraph, UnknownAssetError
from tests.factories import graph


def test_direct_and_transitive_neighbours():
    g = graph()
    assert g.direct_upstream("fct_orders") == ["stg_orders"]
    assert g.direct_downstream("stg_orders") == ["fct_orders", "int_campaign_orders"]
    assert g.all_upstream("executive_dashboard") == [
        "fct_orders", "fin_revenue_close", "raw_orders", "rpt_executive_kpis", "stg_orders"]
    assert "ticket_channel_mix" not in g.all_downstream("raw_orders")


def test_depths_are_minimum_hop_counts():
    depths = graph().downstream_depths("stg_orders")
    assert depths["fct_orders"] == 1
    assert depths["executive_dashboard"] == 4
    assert depths["customer_console"] == 3
    assert graph().upstream_depths("fct_orders") == {"stg_orders": 1, "raw_orders": 2}


def test_column_scoped_propagation_prunes_the_first_hop_only():
    g = graph()
    # Only the campaign model reads campaign_id, so finance and executive outputs are untouched.
    assert set(g.downstream_depths("stg_orders", ["campaign_id"])) == {"int_campaign_orders", "campaign_report"}
    # total_amount flows into fct_orders and from there (row-wise) into everything it feeds.
    reached = set(g.downstream_depths("stg_orders", ["total_amount"]))
    assert {"fct_orders", "executive_dashboard", "customer_console"} <= reached
    assert "int_campaign_orders" not in reached
    assert g.unreferenced_consumers("stg_orders", ["total_amount"]) == ["int_campaign_orders"]


def test_unknown_column_references_are_treated_as_referenced():
    g = graph()  # raw_orders -> stg_orders has no column metadata
    assert "stg_orders" in g.downstream_depths("raw_orders", ["anything"])


def test_paths_between_assets():
    g = graph()
    assert g.shortest_path("raw_orders", "executive_dashboard") == [
        "raw_orders", "stg_orders", "fct_orders", "fin_revenue_close", "rpt_executive_kpis", "executive_dashboard"]
    assert g.shortest_path("raw_tickets", "executive_dashboard") is None
    assert g.all_paths("stg_orders", "customer_console") == [["stg_orders", "fct_orders", "customer_ltv",
                                                              "customer_console"]]
    assert g.all_paths("stg_orders", "stg_orders") == []


def test_neighbourhood_and_longest_chain():
    g = graph()
    hood = g.neighbourhood("fct_orders", upstream_depth=1, downstream_depth=1)
    assert {n.name for n in hood.nodes()} == {"stg_orders", "fct_orders", "fin_revenue_close", "customer_ltv"}
    assert hood.edge_count == 3
    assert g.longest_chain()[0] == "raw_orders"
    assert g.longest_chain()[-1] == "executive_dashboard"


def test_unknown_assets_and_dangling_edges():
    g = LineageGraph([AssetNode("a", "model", "staging")], [Edge("a", "missing")])
    assert g.edge_count == 0
    with pytest.raises(UnknownAssetError):
        g.all_downstream("missing")


def test_cycles_are_rejected():
    nodes = [AssetNode("a", "model", "staging"), AssetNode("b", "model", "mart")]
    with pytest.raises(DataTrustError, match="cycle"):
        LineageGraph(nodes, [Edge("a", "b"), Edge("b", "a")])
