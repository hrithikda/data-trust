"""Lineage: how an asset fits into the platform, from source system to business output."""

from __future__ import annotations

import streamlit as st
import ui

st.title("Lineage")
ui.guard("assets")
service = ui.lineage()
stats = ui.run(service.stats)

c = st.columns(4)
c[0].metric("Assets in graph", stats["assets"])
c[1].metric("Dependencies", stats["edges"])
c[2].metric("Deepest chain", f"{stats['max_depth']} hops")
c[3].metric("Layers", len(stats["by_layer"]))
st.caption("Built from dbt's manifest (`ref`, `source` and exposure dependencies), not hand-maintained. Deepest chain: "
           + " → ".join(stats["longest_chain"]))

explore, paths, full = st.tabs(["Explore an asset", "Paths between assets", "Whole platform"])
names = sorted(n.name for n in service.graph.nodes())
with explore:
    cols = st.columns([3, 1, 1])
    name = cols[0].selectbox("Asset", names, index=ui.query_choice("asset", names, "stg_shopfront__orders"))
    ui.sync_query("asset", name)
    up = cols[1].slider("Upstream hops", 0, 6, 2)
    down = cols[2].slider("Downstream hops", 0, 11, 3)
    lineage = ui.run(lambda: service.asset_lineage(name, up, down))
    st.graphviz_chart(lineage.dot, width="stretch")
    st.caption("Cylinders are raw source tables, notes are business outputs (dbt exposures); bold text and a thick border mark critical assets.")
    a, b = st.columns(2)
    with a:
        st.markdown(f"**Direct upstream:** {', '.join(lineage.direct_upstream) or 'none (source)'}")
        st.dataframe(ui.frame(lineage.upstream), hide_index=True, width="stretch")
    with b:
        st.markdown(f"**Direct downstream:** {', '.join(lineage.direct_downstream) or 'none (leaf)'}")
        st.dataframe(ui.frame(lineage.downstream), hide_index=True, width="stretch")
    ui.page_link("impact", "What breaks if this asset is wrong?", ":material/crisis_alert:", asset=name)

with paths:
    cols = st.columns(2)
    source = cols[0].selectbox("From", names, index=names.index("shopfront_orders") if "shopfront_orders" in names
                               else 0)
    target = cols[1].selectbox("To", names, index=names.index("executive_kpi_dashboard")
                               if "executive_kpi_dashboard" in names else 0)
    result = ui.run(lambda: service.paths(source, target))
    if not result["paths"]:
        st.info(f"No dependency path leads from {source} to {target}. "
                "(Paths follow data flow: upstream to downstream.)")
    else:
        st.write(f"{len(result['paths'])} path(s), shortest has {len(result['paths'][0]) - 1} hops:")
        for path in result["paths"]:
            st.markdown("- " + " → ".join(f"`{p}`" for p in path))
        st.graphviz_chart(result["dot"], width="stretch")

with full:
    st.graphviz_chart(service.full_dot(), width="stretch")
