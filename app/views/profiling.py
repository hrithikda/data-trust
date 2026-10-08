"""Profiling: size, freshness and column statistics computed generically for every relation."""

from __future__ import annotations

import json

import plotly.express as px
import streamlit as st
import ui

st.title("Profiling")
ui.guard("assets", "profile_runs")
service = ui.profiling()
overview = ui.frame(ui.run(service.freshness_overview))

st.subheader("Size and freshness")
st.caption("Freshness = newest value of the dataset's freshness column (ignoring future-dated values) versus the "
           "snapshot time, compared with its SLA. Datasets without an SLA show 'unknown'.")
counts = overview["freshness_status"].value_counts()
c = st.columns(4)
c[0].metric("Profiled datasets", len(overview))
c[1].metric("Fresh", int(counts.get("fresh", 0)))
c[2].metric("Stale", int(counts.get("stale", 0)))
c[3].metric("No SLA", int(counts.get("unknown", 0)))
st.dataframe(overview, hide_index=True, width="stretch", column_order=[
    "asset", "layer", "owner", "row_count", "column_count", "freshness_column", "freshest_value",
    "freshness_lag_hours", "freshness_sla_hours", "freshness_status", "duration_ms"],
    column_config={"freshness_lag_hours": st.column_config.NumberColumn("Lag (h)", format="%.1f"),
                   "freshness_sla_hours": "SLA (h)", "duration_ms": "Profile ms"})

st.divider()
names = overview["asset"].tolist()
name = st.selectbox("Column profiles for", names, index=ui.query_choice("asset", names, "stg_shopfront__orders"))
ui.sync_query("asset", name)
profiles = ui.frame(ui.run(lambda: service.column_profiles(name)))

left, right = st.columns([3, 2])
with left:
    st.dataframe(profiles, hide_index=True, width="stretch", column_order=[
        "column_name", "data_type", "is_critical", "null_count", "null_rate", "distinct_count", "uniqueness",
        "min_value", "max_value", "mean_value", "median_value", "stddev_value"],
        column_config={"null_rate": st.column_config.NumberColumn("Null rate", format="percent"),
                       "uniqueness": st.column_config.NumberColumn("Uniqueness", format="percent"),
                       "is_critical": st.column_config.CheckboxColumn("CDE"),
                       "mean_value": st.column_config.NumberColumn("Mean", format="%.2f"),
                       "median_value": st.column_config.NumberColumn("Median", format="%.2f"),
                       "stddev_value": st.column_config.NumberColumn("Std dev", format="%.2f")})
with right:
    fig = px.bar(profiles, x="null_rate", y="column_name", orientation="h", color="is_critical",
                 color_discrete_map={True: "#d64545", False: "#3d7bd9"},
                 labels={"null_rate": "Null rate", "column_name": "", "is_critical": "CDE"})
    fig.update_layout(height=max(260, 22 * len(profiles)), margin={"l": 10, "r": 10, "t": 30, "b": 10},
                      xaxis_tickformat=".1%", title="Null rate by column", yaxis={"autorange": "reversed"})
    st.plotly_chart(fig, width="stretch")

column = st.selectbox("Inspect column", profiles["column_name"].tolist())
col = profiles[profiles["column_name"] == column].iloc[0]
a, b = st.columns([2, 3])
with a:
    st.markdown(f"**{column}** · `{col['data_type']}`" + (" · critical data element" if col["is_critical"] else ""))
    st.write(f"Nulls: {int(col['null_count']):,} ({col['null_rate']:.2%}) · distinct: {int(col['distinct_count']):,} "
             f"· uniqueness {col['uniqueness']:.2%}")
    if col["contains_pii"]:
        st.info("PII column: values are never stored by the profiler; only counts are shown.")
    else:
        st.write(f"Range: `{col['min_value']}` to `{col['max_value']}`")
        samples = col["sample_values"] if isinstance(col["sample_values"], list) else json.loads(col["sample_values"])
        st.caption("Representative values: " + ", ".join(f"`{s}`" for s in samples))
with b:
    top = col["top_values"] if isinstance(col["top_values"], list) else json.loads(col["top_values"])
    if top:
        fig = px.bar(ui.frame(top), x="count", y="value", orientation="h", title="Most common values")
        fig.update_layout(height=260, margin={"l": 10, "r": 10, "t": 30, "b": 10}, yaxis={"autorange": "reversed",
                                                                                            "type": "category"})
        st.plotly_chart(fig, width="stretch")
    elif not col["contains_pii"]:
        st.caption("Every value is distinct, so there are no common values to show.")

history = ui.frame(service.history(name))
if len(history) > 1:
    st.subheader("Across profile runs")
    st.plotly_chart(px.line(history, x="started_at", y="row_count", markers=True, height=260), width="stretch")
