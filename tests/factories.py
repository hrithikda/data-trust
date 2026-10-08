"""Small in-memory metadata used by unit tests.

            raw_orders
                |
            stg_orders ---------------(campaign_id)--> int_campaign_orders --> campaign_report
                |   (order_id, customer_id, total_amount)
            fct_orders --> fin_revenue_close --> rpt_executive_kpis --> executive_dashboard
                |
            customer_ltv --> customer_console (customer-facing)

    raw_tickets --> stg_tickets --(channel)--> ticket_channel_mix
"""

from __future__ import annotations

from datatrust.impact.analysis import ImpactAnalyzer, MetadataContext
from datatrust.lineage.graph import AssetNode, Edge, LineageGraph

NODES = [
    AssetNode("raw_orders", "source", "raw", "critical", "commerce", "Platform Engineering", source_system="Shopfront"),
    AssetNode("stg_orders", "model", "staging", "critical", "commerce", "Commerce"),
    AssetNode("int_campaign_orders", "model", "intermediate", "low", "growth", "Growth Analytics"),
    AssetNode("campaign_report", "model", "mart", "low", "growth", "Growth Analytics"),
    AssetNode("fct_orders", "model", "mart", "critical", "commerce", "Commerce", is_financial_reporting=True),
    AssetNode("fin_revenue_close", "model", "mart", "critical", "finance", "Finance Analytics",
              is_financial_reporting=True),
    AssetNode("rpt_executive_kpis", "model", "mart", "critical", "executive", "Finance Analytics"),
    AssetNode("executive_dashboard", "exposure", "exposure", "critical", "executive", "Finance Analytics",
              audience="executive"),
    AssetNode("customer_ltv", "model", "mart", "high", "customer", "Customer Data"),
    AssetNode("customer_console", "exposure", "exposure", "high", "customer", "Customer Experience",
              is_customer_facing=True),
    AssetNode("raw_tickets", "source", "raw", "low", "support", "Platform Engineering", source_system="Deskline"),
    AssetNode("stg_tickets", "model", "staging", "low", "support", "Customer Experience"),
    AssetNode("ticket_channel_mix", "model", "mart", "low", "support", "Customer Experience"),
]

EDGES = [
    Edge("raw_orders", "stg_orders"),
    Edge("stg_orders", "int_campaign_orders", ("campaign_id", "order_id")),
    Edge("int_campaign_orders", "campaign_report"),
    Edge("stg_orders", "fct_orders", ("order_id", "customer_id", "total_amount")),
    Edge("fct_orders", "fin_revenue_close"),
    Edge("fin_revenue_close", "rpt_executive_kpis"),
    Edge("rpt_executive_kpis", "executive_dashboard"),
    Edge("fct_orders", "customer_ltv"),
    Edge("customer_ltv", "customer_console"),
    Edge("raw_tickets", "stg_tickets"),
    Edge("stg_tickets", "ticket_channel_mix", ("ticket_id", "channel")),
]


def graph() -> LineageGraph:
    return LineageGraph(NODES, EDGES)


def analyzer() -> ImpactAnalyzer:
    context = MetadataContext(
        graph=graph(),
        asset_terms={"fct_orders": {"Net Revenue"}},
        column_terms={("stg_orders", "customer_id"): {"Customer"}, ("stg_orders", "total_amount"): {"Order Total"}},
        asset_rules={"stg_orders": ["orders_order_id_unique"]},
    )
    return ImpactAnalyzer(context)
