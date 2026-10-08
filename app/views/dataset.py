"""Dataset detail: meaning, ownership, origin, structure, quality and lineage of one asset."""

from __future__ import annotations

import streamlit as st
import ui

st.title("Dataset detail")
ui.guard("assets")
service = ui.catalog()
names = ui.run(service.asset_names)
name = st.selectbox("Dataset", names, index=ui.query_choice("asset", names, "fct_orders"))
ui.sync_query("asset", name)
detail = ui.run(lambda: service.asset_detail(name))
asset, profile = detail["asset"], detail["profile"]

ui.badges((asset["layer"], ui.LAYER_BADGE[asset["layer"]]), (asset["asset_type"], "gray"), (asset["domain"], "gray"),
          (f"{asset['criticality']} criticality", ui.SEVERITY_BADGE.get(asset["criticality"], "gray")),
          *([("contains PII", "violet")] if asset["contains_pii"] else []),
          *([("financial reporting", "blue")] if asset["is_financial_reporting"] else []),
          *([("customer-facing", "orange")] if asset["is_customer_facing"] else []))
st.write(asset["description"] or "_No description._")

c = st.columns(6)
c[0].metric("Quality health", ui.health_label(detail["health"]) if detail["health"] is not None else "no rules",
            help="Severity-weighted share of this dataset's rules passing in the latest run. Rules run on staging "
                 "models; the Quality tab lists upstream issues whose blast radius reaches this dataset.")
c[1].metric("Open issues", len([i for i in detail["issues"] if i["status"] in ("open", "investigating")]))
c[2].metric("Rows", ui.num(profile["row_count"]) if profile else "n/a")
if profile and profile["freshness_lag_hours"] is not None:
    c[3].metric("Freshness lag", f"{float(profile['freshness_lag_hours']):.1f} h",
                help=f"Newest {asset['freshness_column']} vs snapshot; SLA "
                     f"{asset['freshness_sla_hours'] or 'none'} h. Status: {profile['freshness_status']}.")
else:
    c[3].metric("Freshness lag", "n/a")
c[4].metric("Upstream assets", len(detail["upstream"]))
c[5].metric("Downstream assets", len(detail["downstream"]))

tabs = st.tabs(["Columns", "Ownership & origin", "Quality", "Lineage", "Glossary", "dbt"])

with tabs[0]:
    columns = ui.frame(detail["columns"])
    if columns.empty:
        st.info("No column metadata.")
    else:
        st.dataframe(columns, hide_index=True, width="stretch", column_order=[
            "column_name", "data_type", "is_critical", "cde_category", "description", "terms", "null_rate",
            "distinct_count", "uniqueness", "min_value", "max_value", "contains_pii", "critical_reason"],
            column_config={
                "column_name": "Column", "data_type": "Type", "is_critical": st.column_config.CheckboxColumn("CDE"),
                "cde_category": "CDE category", "terms": st.column_config.ListColumn("Glossary"),
                "null_rate": st.column_config.NumberColumn("Null rate", format="percent"),
                "uniqueness": st.column_config.NumberColumn("Uniqueness", format="percent"),
                "contains_pii": st.column_config.CheckboxColumn("PII"), "critical_reason": "Why critical",
            })
        if asset["primary_key"]:
            st.caption(f"Primary key: `{', '.join(asset['primary_key'])}`")

with tabs[1]:
    a, b = st.columns(2)
    with a:
        st.subheader("Owner")
        st.markdown(f"**{asset['owner'] or 'Unassigned'}**")
        if asset["owner"]:
            st.markdown(f"Lead: {asset['lead_name']}  \nEmail: {asset['owner_email']}  \n"
                        f"Slack: `{asset['slack_channel']}`  \nOn-call: {asset['on_call_rotation'] or 'n/a'}")
    with b:
        st.subheader("Where the data comes from")
        if asset["source_system"]:
            st.markdown(f"**{asset['source_system']}** ({asset['system_type']}, {asset['vendor']})  \n"
                        f"Object: `{asset['source_object']}`  \nIngestion: {asset['ingestion_method']} · "
                        f"{asset['load_frequency']}")
            if asset["extraction_notes"]:
                st.caption(asset["extraction_notes"])
        elif detail["origin_systems"]:
            st.write("Built from these operational systems (via lineage):")
            st.dataframe(ui.frame(detail["origin_systems"]), hide_index=True)
        else:
            st.write("No upstream source system recorded.")
    consumers = [d for d in detail["downstream"] if d["type"] == "exposure"]
    if consumers:
        st.subheader("Business outputs that depend on it")
        st.dataframe(ui.frame(consumers, ["name", "depth", "criticality", "owner"]), hide_index=True,
                     column_config={"depth": "Hops away"})

with tabs[2]:
    if detail["issues"]:
        st.subheader("Unresolved issues")
        for issue in detail["issues"]:
            ui.page_link("issues", f"{issue['priority_band']} · {issue['priority_score']:.0f} · {issue['issue_key']} "
                                   f"{issue['title']} ({issue['status']})", ":material/bug_report:",
                         issue=issue["issue_key"])
    if detail["inherited_issues"]:
        st.subheader("Upstream issues reaching this dataset")
        st.caption("Defects detected in upstream datasets whose blast radius includes this one.")
        for issue in detail["inherited_issues"]:
            ui.page_link("issues", f"{issue['priority_band']} · {issue['priority_score']:.0f} · {issue['issue_key']} "
                                   f"{issue['title']} — from {issue['source_asset']}, {issue['depth']} hop(s) up",
                         ":material/call_merge:", issue=issue["issue_key"])
    rules = ui.frame(detail["rules"])
    st.subheader("Quality rules (latest run)")
    if rules.empty:
        st.info("No quality rules target this dataset directly. Downstream datasets inherit protection from rules "
                "on their staging inputs.")
    else:
        st.dataframe(rules, hide_index=True, width="stretch", column_config={
            "failure_rate": st.column_config.NumberColumn("Failure rate", format="percent"),
            "column_names": st.column_config.ListColumn("Columns"), "rulesets": st.column_config.ListColumn("Rulesets"),
        })

with tabs[3]:
    lineage = ui.run(lambda: ui.lineage().asset_lineage(name, 2, 2))
    st.graphviz_chart(lineage.dot, width="stretch")
    a, b = st.columns(2)
    a.markdown("**Upstream** (all, by hops)")
    a.dataframe(ui.frame(detail["upstream"]), hide_index=True, width="stretch")
    b.markdown("**Downstream** (all, by hops)")
    b.dataframe(ui.frame(detail["downstream"]), hide_index=True, width="stretch")
    ui.page_link("impact", "Open impact analysis for this dataset", ":material/crisis_alert:", asset=name)

with tabs[4]:
    if not detail["terms"]:
        st.info("No glossary terms are linked to this dataset.")
    for term in detail["terms"]:
        with st.container(border=True):
            st.markdown(f"**{term['name']}** · _{term['status']}_")
            st.write(term["definition"])
            if term["columns"]:
                st.caption("Columns: " + ", ".join(f"`{c}`" for c in term["columns"]))

with tabs[5]:
    st.caption(f"dbt unique id `{asset['unique_id']}` · materialized as {asset['materialization'] or 'n/a'} · "
               f"{asset['file_path'] or ''}")
    tests = ui.frame(detail["dbt_tests"])
    if tests.empty:
        st.info("No dbt tests attached.")
    else:
        st.dataframe(tests, hide_index=True, width="stretch")
