"""Data catalog: find the right dataset and see at a glance whether it can be trusted."""

from __future__ import annotations

import streamlit as st
import ui

st.title("Data catalog")
ui.guard("assets")
service = ui.catalog()
options = ui.run(service.filter_options)

query = st.text_input("Search datasets", placeholder="e.g. revenue, order_id, customer lifetime value, payments, finance",
                      help="Matches asset names, column names, descriptions, glossary terms, owners, domains and "
                           "source systems. Every word must match somewhere.")
cols = st.columns(5)
layers = cols[0].multiselect("Layer", options["layer"])
domains = cols[1].multiselect("Domain", options["domain"])
owners = cols[2].multiselect("Owner", options["owner"])
criticalities = cols[3].multiselect("Criticality", options["criticality"])
with cols[4]:
    st.write("")
    only_issues = st.toggle("Only with open issues")

hits = ui.run(lambda: service.search(query, layers, domains, owners, criticalities, only_issues))
st.caption(f"{len(hits)} dataset(s)")
if not hits:
    st.info("Nothing matches. Try a business term (e.g. 'refund') or a column name (e.g. 'customer_id').")
    st.stop()

view = st.segmented_control("View", ["Cards", "Table"], default="Cards", label_visibility="collapsed")
if view == "Table":
    table = ui.frame([h.__dict__ for h in hits], ["name", "layer", "domain", "owner", "criticality", "source_system",
                                                   "health", "open_issues", "freshness_status", "row_count",
                                                   "column_count", "critical_fields", "reasons"])
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "health": st.column_config.ProgressColumn("Health", min_value=0, max_value=100, format="%.0f"),
        "reasons": st.column_config.ListColumn("Why it matched"),
    })
    st.stop()

for hit in hits:
    with st.container(border=True):
        top = st.columns([4, 1, 1, 1])
        with top[0]:
            ui.page_link("dataset", f"**{hit.name}**", ":material/table_view:", asset=hit.name)
            ui.badges((hit.layer, ui.LAYER_BADGE[hit.layer]), (hit.domain, "gray"),
                      (f"{hit.criticality} criticality", ui.SEVERITY_BADGE.get(hit.criticality, "gray")),
                      *([(f"freshness: {hit.freshness_status}", ui.FRESHNESS_BADGE[hit.freshness_status])]
                        if hit.freshness_status and hit.freshness_status != "unknown" else []))
        top[1].metric("Health", ui.health_label(hit.health) if hit.health is not None else "no rules",
                      help="Rules run on staging models, where defects enter. Dataset detail shows which "
                           "upstream issues reach a downstream dataset.")
        top[2].metric("Open issues", hit.open_issues)
        top[3].metric("Rows", ui.num(hit.row_count) if hit.row_count is not None else "n/a")
        st.write(hit.description or "_No description._")
        meta = [f"Owner: **{hit.owner or 'unassigned'}**"]
        if hit.source_system:
            meta.append(f"Source: **{hit.source_system}**")
        meta.append(f"{hit.column_count} columns, {hit.critical_fields} critical")
        st.caption(" · ".join(meta))
        if hit.reasons and query:
            st.caption("Matched on " + "; ".join(hit.reasons))
