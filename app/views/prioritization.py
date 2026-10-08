"""Prioritization: why issues rank where they do, and why failure counts alone mislead."""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import ui

from datatrust.errors import DataTrustError

st.title("Prioritization")
ui.guard("assets", "issues")
service = ui.issues()
queue = ui.frame(service.queue(["open", "investigating", "accepted"]))
if queue.empty:
    st.success("No unresolved issues.")
    st.stop()

st.caption("Priority = defect factors (severity, affected volume and share, dataset and field criticality; 55 pts) + "
           "blast-radius factors (downstream assets, critical consumers, financial, executive or customer-facing "
           "outputs, teams; 45 pts). Failure count and failure share together are worth at most 15 points.")

st.subheader("Priority versus affected records")
queue["bubble"] = queue["downstream_count"] + 2  # issues with no consumers must stay visible
fig = px.scatter(queue, x="affected_records", y="priority_score", color="priority_band", size="bubble",
                 size_max=28, log_x=True, hover_name="issue_key", range_y=[0, 100],
                 hover_data={"title": True, "asset": True, "affected_records": ":,", "downstream_count": True,
                             "bubble": False},
                 color_discrete_map=ui.BAND_COLORS, category_orders={"priority_band": ["P1", "P2", "P3", "P4"]},
                 labels={"affected_records": "Affected records (log scale)", "priority_score": "Priority score"})
for threshold, band in ((80, "P1"), (65, "P2"), (45, "P3")):
    fig.add_hline(y=threshold, line_dash="dot", line_color="#cbd2d9", annotation_text=band,
                  annotation_position="right")
fig.update_layout(height=420, margin={"l": 10, "r": 40, "t": 10, "b": 10}, legend_title="Band")
st.plotly_chart(fig, width="stretch")
corr = queue["priority_score"].rank().corr(queue["affected_records"].rank())  # Spearman, without scipy
st.caption(f"Bubble size = downstream assets. Spearman rank correlation between affected records and priority: "
           f"{corr:+.2f}. "
           "The ordering is driven by where the broken data flows, not by how much of it there is.")

st.subheader("Small and important versus large and harmless")
small = queue.sort_values(["priority_score", "affected_records"], ascending=[False, True])
small = small[small["affected_records"] <= queue["affected_records"].median()].iloc[0]
large = queue.sort_values("affected_records", ascending=False).iloc[0]
cols = st.columns(2)
for col, row, label in ((cols[0], small, "Top-priority small defect (at most median size)"),
                         (cols[1], large, "Largest defect")):
    detail = service.detail(row["issue_key"])
    breakdown = ui.frame(detail["priority_breakdown"])
    with col, st.container(border=True):
        st.markdown(f"**{label}**")
        ui.page_link("issues", f"{row['issue_key']} · {row['title']}", ":material/bug_report:", issue=row["issue_key"])
        m = st.columns(3)
        m[0].metric("Affected records", f"{int(row['affected_records']):,}")
        m[1].metric("Priority", f"{row['priority_score']:.1f}", help=row["priority_band"])
        m[2].metric("Downstream", int(row["downstream_count"] or 0))
        grouped = breakdown.groupby("group")["points"].sum()
        st.write(f"Defect points {grouped.get('defect', 0):.1f} · blast-radius points "
                 f"{grouped.get('blast_radius', 0):.1f}")
        for reason in breakdown.sort_values("points", ascending=False)["reason"].head(4):
            st.caption("• " + reason)

st.subheader("Score composition of every unresolved issue")
rows = []
for key in queue["issue_key"]:
    for factor in service.detail(key)["priority_breakdown"]:
        rows.append({"issue": key, "factor": factor["label"], "points": factor["points"], "group": factor["group"]})
comp = ui.frame(rows)
fig = px.bar(comp, x="points", y="issue", color="factor", orientation="h",
             category_orders={"issue": queue["issue_key"].tolist()})
fig.update_layout(height=max(320, 24 * len(queue)), margin={"l": 10, "r": 10, "t": 10, "b": 10},
                  yaxis={"title": None}, legend_title=None, xaxis_range=[0, 100])
st.plotly_chart(fig, width="stretch")

st.subheader("What-if: score a hypothetical defect")
st.caption("Uses the same model and lineage as real issues. Try a few broken `order_id` values in staging versus "
           "thousands of bad values in a descriptive ticket field.")
impact_service = ui.impact()
names = ui.catalog().asset_names()
w = st.columns([2, 2, 1, 1, 1])
asset = w[0].selectbox("Dataset", names, index=names.index("stg_shopfront__orders")
                       if "stg_shopfront__orders" in names else 0)
columns = [c["column_name"] for c in impact_service.columns(asset)]
picked = w[1].multiselect("Broken columns", columns, default=columns[:1])
severity = w[2].selectbox("Severity", ["critical", "high", "medium", "low"], index=1)
scanned = w[3].number_input("Rows scanned", min_value=1, value=20000, step=1000)
failed = w[4].number_input("Rows failing", min_value=0, value=5, step=1)
try:
    result = impact_service.what_if(asset, picked, severity, int(failed), int(scanned))
except DataTrustError as exc:
    st.warning(str(exc))
    st.stop()
st.metric("Hypothetical priority", f"{result.score:.1f}", help=result.band)
factors = ui.frame([f.__dict__ for f in result.factors])
fig = go.Figure(go.Bar(y=factors["label"], x=factors["points"], orientation="h", text=factors["reason"],
                       marker_color=["#3d7bd9" if g == "defect" else "#d64545" for g in factors["group"]],
                       hovertemplate="%{y}: %{x:.1f}<br>%{text}<extra></extra>", textposition="none"))
fig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis={"autorange": "reversed"})
st.plotly_chart(fig, width="stretch")
