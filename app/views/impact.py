"""Impact analysis: what would break if this asset (or these columns) became incorrect?"""

from __future__ import annotations

import plotly.express as px
import streamlit as st
import ui

st.title("Impact analysis")
ui.guard("assets")
service = ui.impact()
names = sorted(n.name for n in service.context.graph.nodes() if n.asset_type != "exposure")
cols = st.columns([2, 3, 1])
asset = cols[0].selectbox("If this dataset is wrong", names,
                          index=ui.query_choice("asset", names, "stg_shopfront__orders"))
ui.sync_query("asset", asset)
column_rows = service.columns(asset)
labels = {c["column_name"]: c["column_name"] + (" ★" if c["is_critical"] else "") for c in column_rows}
picked = cols[1].multiselect("...in these columns (empty = whole dataset)", list(labels),
                             format_func=labels.get)
with cols[2]:
    st.write("")
    row_level = st.toggle("Row-level defect", help="Duplicated or missing rows affect every consumer regardless "
                                                   "of which columns it reads.")

view = ui.run(lambda: service.analyze(asset, picked, row_level))
report = view.report

c = st.columns(6)
c[0].metric("Impact score", f"{view.impact_score:.0f}", help=f"Blast radius only (0-100): {view.impact_level}")
c[1].metric("Downstream assets", report.downstream_count)
c[2].metric("Critical assets", len(report.critical_assets))
c[3].metric("Marts", len(report.marts))
c[4].metric("Business outputs", len(report.exposures))
c[5].metric("Hops to executive", report.hops_to_executive or "none")
flags = []
if report.financial_reporting_affected:
    flags.append(("financial reporting exposed", "red"))
if report.executive_affected:
    flags.append(("executive reporting exposed", "red"))
if report.customer_facing_affected:
    flags.append(("customer-facing output exposed", "orange"))
if flags:
    ui.badges(*flags)
if report.unaffected_consumers:
    st.caption("Pruned by column-level lineage (do not read the selected columns): "
               + ", ".join(report.unaffected_consumers))

if not report.impacted:
    st.success(f"Nothing downstream depends on {'these columns of ' if picked else ''}{asset}.")
else:
    st.graphviz_chart(view.dot, width="stretch")
    impacted = ui.frame([i.__dict__ for i in report.impacted])
    impacted["path"] = impacted["path"].map(lambda p: " → ".join(p))
    tabs = st.tabs(["Affected assets", "Teams to notify", "Business terms", "Rules & open issues"])
    with tabs[0]:
        depth = px.histogram(impacted, x="depth", color="layer", title="Affected assets by distance (hops)")
        depth.update_layout(height=260, margin={"l": 10, "r": 10, "t": 40, "b": 10}, bargap=0.2)
        st.plotly_chart(depth, width="stretch")
        st.dataframe(impacted, hide_index=True, width="stretch", column_order=[
            "name", "depth", "layer", "asset_type", "criticality", "owner_team", "is_financial_reporting",
            "is_customer_facing", "is_executive", "path"])
    with tabs[1]:
        st.dataframe(ui.frame(view.owners), hide_index=True, width="stretch",
                     column_config={"owned_assets": st.column_config.ListColumn("Affected assets they own")})
    with tabs[2]:
        for term in view.terms:
            st.markdown(f"**{term['name']}** ({term['domain']}, {term['status']}): {term['definition']}")
    with tabs[3]:
        st.markdown("**Quality rules on this dataset**")
        st.dataframe(ui.frame(view.rules), hide_index=True, width="stretch")
        st.markdown("**Open issues on this dataset or its upstream inputs**")
        if not view.open_issues:
            st.caption("None.")
        for issue in view.open_issues:
            ui.page_link("issues", f"{issue['priority_band']} {issue['issue_key']} · {issue['title']}",
                         ":material/bug_report:", issue=issue["issue_key"])
st.caption(f"Upstream source systems: {', '.join(report.upstream_sources) or 'n/a'} · owner of {asset}: "
           f"{report.owner_team or 'unassigned'}")
