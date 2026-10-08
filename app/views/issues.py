"""Issue investigation: everything needed to understand and route one quality issue."""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
import ui

from datatrust.errors import DataTrustError
from datatrust.services.lineage import to_dot

st.title("Issue investigation")
ui.guard("assets", "issues")
service = ui.issues()

with st.expander("Issue queue", expanded="issue" not in st.query_params):
    f = st.columns(3)
    statuses = f[0].multiselect("Status", ["open", "investigating", "accepted", "resolved"],
                                default=["open", "investigating", "accepted"])
    bands = f[1].multiselect("Priority band", ["P1", "P2", "P3", "P4"])
    owners = f[2].multiselect("Owner", service.owners())
    queue = ui.frame(service.queue(statuses, bands, owners))
    if queue.empty:
        st.info("No issues match.")
    else:
        selection = st.dataframe(
            queue, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
            column_order=["issue_key", "priority_band", "priority_score", "status", "title", "owner", "severity",
                          "affected_records", "downstream_count"],
            column_config={"priority_score": st.column_config.ProgressColumn("Priority", min_value=0, max_value=100,
                                                                             format="%.1f")})
        if selection.selection.rows:
            ui.sync_query("issue", queue.iloc[selection.selection.rows[0]]["issue_key"])

keys = service.keys()
if not keys:
    st.info("No issues have been raised yet.")
    st.stop()
key = st.selectbox("Issue", keys, index=ui.query_choice("issue", keys))
ui.sync_query("issue", key)
issue = ui.run(lambda: service.detail(key))
impact = issue["impact_summary"]

st.header(f"{issue['issue_key']} · {issue['rule_name']}")
ui.badges((issue["status"], ui.STATUS_BADGE[issue["status"]]),
          (f"{issue['priority_band']} · {issue['priority_score']:.1f}", "red" if issue["priority_band"] == "P1" else
           "orange" if issue["priority_band"] == "P2" else "gray"),
          (f"{issue['severity']} severity", ui.SEVERITY_BADGE[issue["severity"]]), (issue["category"], "gray"))
st.write(issue["description"])

c = st.columns(6)
c[0].metric("Affected records", f"{issue['affected_records']:,}")
c[1].metric("Share of rows", ui.pct(issue["affected_rate"]))
c[2].metric("Downstream assets", impact.get("downstream_count", 0))
c[3].metric("Business outputs", len(impact.get("exposures", [])))
c[4].metric("Hops to executive", impact.get("hops_to_executive") or "none")
c[5].metric("Teams affected", len(impact.get("teams_affected", [])))
st.caption(f"Dataset {issue['asset']} ({issue['asset_criticality']} criticality) · first detected "
           f"{issue['first_detected_at']:%d %b %Y} · last detected {issue['last_detected_at']:%d %b %Y} · "
           f"seen in {issue['occurrences']} run(s)")
ui.page_link("dataset", f"Open dataset {issue['asset']}", ":material/table_view:", asset=issue["asset"])

tabs = st.tabs(["Evidence", "Why this priority", "Blast radius", "Owners", "History & activity"])
with tabs[0]:
    st.markdown(f"**Rule** `{issue['rule_key']}` ({issue['rule_type']}): {issue['rule_description']}")
    st.markdown(f"**Business impact:** {issue['business_impact']}")
    if issue["critical_columns"]:
        st.markdown("**Critical data elements involved:** " + ", ".join(f"`{c}`" for c in issue["critical_columns"]))
    st.markdown("**Sample failing records**")
    st.dataframe(ui.samples_frame(issue["sample_failures"], issue["column_names"]), hide_index=True, width="stretch")
    st.caption(f"{issue['records_scanned']:,} rows scanned in the latest run.")

with tabs[1]:
    breakdown = ui.frame(issue["priority_breakdown"])
    st.markdown(f"Score **{issue['priority_score']:.1f} / 100** is the sum of capped factors. "
                f"Defect factors: **{breakdown.loc[breakdown.group == 'defect', 'points'].sum():.1f}**, "
                f"blast-radius factors: **{breakdown.loc[breakdown.group == 'blast_radius', 'points'].sum():.1f}**.")
    fig = go.Figure()
    fig.add_trace(go.Bar(y=breakdown["label"], x=breakdown["max_points"], orientation="h", name="Maximum",
                         marker_color="rgba(154,165,177,0.25)", hoverinfo="skip"))
    fig.add_trace(go.Bar(y=breakdown["label"], x=breakdown["points"], orientation="h", name="Awarded",
                         marker_color=["#3d7bd9" if g == "defect" else "#d64545" for g in breakdown["group"]],
                         text=breakdown["reason"], textposition="none",
                         hovertemplate="%{y}: %{x:.1f} pts<br>%{text}<extra></extra>"))
    fig.update_layout(barmode="overlay", height=380, margin={"l": 10, "r": 10, "t": 10, "b": 10},
                      yaxis={"autorange": "reversed"}, xaxis_title="Points", showlegend=False)
    st.plotly_chart(fig, width="stretch")
    st.dataframe(breakdown, hide_index=True, width="stretch",
                 column_order=["label", "group", "points", "max_points", "reason"])
    st.caption("Blue = properties of the broken records; red = what depends on them. Weights live in "
               "`config/priority_model.yml`.")

with tabs[2]:
    impacts = ui.frame(issue["impacts"])
    if impacts.empty:
        st.success("Nothing downstream reads the affected data.")
    else:
        graph = ui.lineage().graph
        keep = {issue["asset"], *impacts["name"]}
        st.graphviz_chart(to_dot(graph.subgraph(keep), focus=issue["asset"], impacted=impacts["name"]),
                          width="stretch")
        if impact.get("unaffected_consumers"):
            st.caption("Not affected (their SQL does not read the broken columns): "
                       + ", ".join(impact["unaffected_consumers"]))
        impacts["path"] = impacts["path"].map(lambda p: " → ".join(p))
        st.dataframe(impacts, hide_index=True, width="stretch", column_order=[
            "name", "depth", "layer", "criticality", "owner", "is_financial_reporting", "is_customer_facing", "path"])
        if impact.get("glossary_terms"):
            st.caption("Business terms affected: " + ", ".join(impact["glossary_terms"]))

with tabs[3]:
    st.dataframe(ui.frame(issue["owner_contacts"]), hide_index=True, width="stretch",
                 column_config={"owns_broken_asset": st.column_config.CheckboxColumn("Owns broken asset")})
    st.caption("Route to the owner of the broken asset; notify teams owning affected downstream assets.")

with tabs[4]:
    history = ui.frame(issue["history"])
    fig = go.Figure(go.Bar(x=history["as_of"], y=history["records_failed"],
                           marker_color=["#d64545" if s == "fail" else "#2f9e63" for s in history["status"]]))
    fig.update_layout(height=240, margin={"l": 10, "r": 10, "t": 10, "b": 10}, yaxis_title="Failing records")
    st.plotly_chart(fig, width="stretch")
    for event in issue["events"]:
        transition = f"{event['from_status'] or '-'} → {event['to_status']}" if event["to_status"] else ""
        when = event["run_as_of"] or event["created_at"]
        st.markdown(f"- **{event['event_type']}** {transition} · {event['actor']} · {when:%d %b %Y %H:%M}"
                    + (f"  \n  {event['note']}" if event["note"] else ""))

st.divider()
st.subheader("Triage")
with st.form("triage", clear_on_submit=True):
    cols = st.columns([1, 1, 3])
    new_status = cols[0].selectbox("Move to", ["(comment only)", *issue["allowed_transitions"]])
    actor = cols[1].text_input("Your name", max_chars=80)
    note = cols[2].text_input("Note", max_chars=2000)
    if st.form_submit_button("Save"):
        try:
            if new_status == "(comment only)":
                service.comment(key, actor, note)
            else:
                service.change_status(key, new_status, actor, note)
            ui.refresh()
            st.rerun()
        except DataTrustError as exc:
            st.error(str(exc))
