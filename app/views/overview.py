"""Platform overview: is the data platform healthy, and what needs attention first?"""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import ui

st.title("Platform overview")
ui.guard("assets")
service = ui.overview()
kpis = ui.run(service.kpis)

st.caption("Copperleaf Coffee Co. analytics warehouse. Health is the severity-weighted share of quality rules "
           "passing in the latest run (a failing rule loses more score the larger its failure rate).")

c = st.columns(6)
c[0].metric("Quality health", ui.health_label(kpis["health"]),
            delta=f"{kpis['health_change_7d']:+.1f} over 7 runs" if kpis["health_change_7d"] is not None else None,
            help="Weighted by rule severity (low 1 - critical 4).")
c[1].metric("Open issues", kpis["open_issues"], help="Open or investigating. Accepted issues are excluded.")
c[2].metric("P1 issues", kpis["p1_issues"], help="Priority score >= 80.")
c[3].metric("Critical-severity issues", kpis["critical_issues"])
c[4].metric("Failing rules", f"{kpis['rules_failing']} / {kpis['rules_executed']}")
c[5].metric("Accepted risks", kpis["accepted_issues"])
c = st.columns(6)
c[0].metric("Datasets", kpis["datasets"])
c[1].metric("Business outputs", kpis["exposures"], help="dbt exposures: dashboards, models and applications.")
c[2].metric("Columns", kpis["columns"])
c[3].metric("Critical data elements", kpis["critical_fields"])
c[4].metric("Glossary terms", kpis["glossary_terms"])
c[5].metric("Owning teams", kpis["teams"])

if kpis["health"] is None:
    st.info("No quality runs yet. Run `datatrust quality backfill` (or `make pipeline`) to populate health.")
    st.stop()

left, right = st.columns([3, 2])
with left:
    st.subheader("Quality health over time")
    trend = ui.frame(service.health_trend())
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=trend["as_of"], y=trend["health"], name="Health", mode="lines+markers",
                             line={"color": "#3d7bd9", "width": 3}))
    fig.add_trace(go.Bar(x=trend["as_of"], y=trend["rules_failed"], name="Failing rules", yaxis="y2",
                         marker_color="rgba(214,69,69,0.35)"))
    fig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10}, hovermode="x unified",
                      yaxis={"title": "Health", "range": [max(0, trend["health"].min() - 10), 100]},
                      yaxis2={"title": "Failing rules", "overlaying": "y", "side": "right", "showgrid": False},
                      legend={"orientation": "h", "y": -0.2})
    st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Recently failing datasets")
    failing = ui.frame(service.failing_assets(8))
    if failing.empty:
        st.success("No dataset has failing rules in the latest run.")
    for row in failing.itertuples():
        with st.container(border=True):
            ui.page_link("dataset", f"**{row.asset}**", ":material/table_view:", asset=row.asset)
            st.caption(f"{row.owner} · {row.failing_rules} failing rule(s) · {int(row.failing_records):,} rows · "
                       f"health {row.health:.0f} · failing since {row.failing_since:%d %b}")

st.subheader("Highest-priority issues")
st.caption("Ranked by impact-based priority: what the broken records feed matters as much as how many there are.")
top = service.top_issues(8)
for issue in top:
    with st.container(border=True):
        cols = st.columns([0.8, 5, 1.4, 1.4])
        cols[0].markdown(f"<div style='font-size:1.6rem;font-weight:700;color:{ui.BAND_COLORS[issue['priority_band']]}'>"
                         f"{issue['priority_score']:.0f}</div><div>{issue['priority_band']}</div>",
                         unsafe_allow_html=True)
        with cols[1]:
            ui.page_link("issues", f"**{issue['issue_key']}** · {issue['title']}", issue=issue["issue_key"])
            exposures = ", ".join(issue["exposures"] or []) or "no business outputs"
            st.caption(f"{issue['asset']} · owner {issue['owner']} · reaches {exposures}")
        cols[2].metric("Affected rows", f"{issue['affected_records']:,}")
        with cols[3]:
            ui.badges((issue["status"], ui.STATUS_BADGE[issue["status"]]),
                      (issue["severity"], ui.SEVERITY_BADGE[issue["severity"]]))

dist = ui.frame(service.issue_distribution())
if not dist.empty:
    a, b = st.columns(2)
    with a:
        st.subheader("Unresolved issues by owner")
        by_owner = dist.groupby(["owner", "priority_band"]).size().reset_index(name="issues")
        fig = px.bar(by_owner, x="issues", y="owner", color="priority_band", orientation="h",
                     color_discrete_map=ui.BAND_COLORS, category_orders={"priority_band": ["P1", "P2", "P3", "P4"]})
        fig.update_layout(height=300, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis_title=None,
                          legend_title="Band")
        st.plotly_chart(fig, width="stretch")
    with b:
        st.subheader("Unresolved issues by category")
        by_cat = dist.groupby(["category", "severity"]).size().reset_index(name="issues")
        fig = px.bar(by_cat, x="issues", y="category", color="severity", orientation="h",
                     color_discrete_map=ui.SEVERITY_COLORS,
                     category_orders={"severity": ["critical", "high", "medium", "low"]})
        fig.update_layout(height=300, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis_title=None,
                          legend_title="Severity")
        st.plotly_chart(fig, width="stretch")

with st.expander("Pipeline activity"):
    events = ui.frame(service.pipeline_events(20), ["created_at", "step", "status", "details"])
    if not events.empty:
        events["details"] = events["details"].map(lambda d: ", ".join(f"{k}={v}" for k, v in d.items()))
    st.dataframe(events, hide_index=True, width="stretch")
