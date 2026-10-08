from tests.factories import analyzer


def test_row_level_defect_reaches_every_consumer():
    report = analyzer().analyze("stg_orders", ["order_id"], row_level=True)
    names = {a.name for a in report.impacted}
    assert {"fct_orders", "int_campaign_orders", "executive_dashboard", "customer_console"} <= names
    assert report.columns == ["order_id"]
    assert report.unaffected_consumers == []
    assert report.direct_consumers == ["fct_orders", "int_campaign_orders"]


def test_column_defect_only_reaches_readers_of_the_column():
    report = analyzer().analyze("stg_orders", ["campaign_id"])
    assert [a.name for a in report.impacted] == ["int_campaign_orders", "campaign_report"]
    assert report.unaffected_consumers == ["fct_orders"]
    assert not report.financial_reporting_affected
    assert not report.executive_affected
    assert report.teams_affected == ["Commerce", "Growth Analytics"]


def test_business_exposure_flags_and_hops():
    report = analyzer().analyze("stg_orders", ["total_amount"])
    assert report.financial_reporting_affected
    assert report.executive_affected
    assert report.customer_facing_affected
    assert report.hops_to_executive == 3  # rpt_executive_kpis (domain executive) is 3 hops away
    assert report.max_depth == 4
    exec_dashboard = next(a for a in report.impacted if a.name == "executive_dashboard")
    assert exec_dashboard.path[0] == "stg_orders" and exec_dashboard.path[-1] == "executive_dashboard"
    assert report.teams_affected[0] == "Commerce"  # owner of the broken asset comes first


def test_glossary_terms_rules_and_sources_are_attached():
    report = analyzer().analyze("stg_orders", ["customer_id"])
    assert "Customer" in report.glossary_terms
    assert "Order Total" not in report.glossary_terms  # column term of an unaffected column
    assert "Net Revenue" in report.glossary_terms      # asset term of an impacted model
    assert report.related_rules == ["orders_order_id_unique"]
    assert report.upstream_sources == ["Shopfront"]


def test_leaf_asset_has_no_blast_radius():
    report = analyzer().analyze("ticket_channel_mix")
    assert report.downstream_count == 0
    assert report.summary()["downstream_count"] == 0
    assert report.hops_to_executive is None
