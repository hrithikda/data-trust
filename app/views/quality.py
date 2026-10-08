"""Data quality: rule results per run, trends by category, and the evidence behind each failure."""

from __future__ import annotations

import plotly.express as px
import streamlit as st
import ui

st.title("Data quality")
ui.guard("assets", "quality_runs")
service = ui.quality()
runs = ui.frame(ui.run(service.runs))

labels = [f"{r.as_of:%Y-%m-%d} · run {r.run_id} · {r.ruleset} · health {r.health:.1f}" for r in runs.itertuples()]
choice = st.selectbox("Quality run", range(len(labels)), format_func=lambda i: labels[i])
run = runs.iloc[choice]
results = ui.frame(service.results(int(run["run_id"])))

c = st.columns(5)
c[0].metric("Health", ui.health_label(run["health"]))
c[1].metric("Rules executed", int(run["rules_executed"]))
c[2].metric("Failing", int(run["rules_failed"]))
c[3].metric("Errored", int(run["rules_errored"]))
c[4].metric("Failing records", f"{int(results['records_failed'].sum()):,}")

trend_tab, results_tab, assets_tab, rules_tab = st.tabs(["Trends", "Rule results", "Health by dataset",
                                                         "Rule catalog"])
with trend_tab:
    cat = ui.frame(service.category_trend())
    a, b = st.columns(2)
    with a:
        fig = px.area(cat, x="as_of", y="failing_rules", color="category", title="Failing rules by category")
        fig.update_layout(height=340, margin={"l": 10, "r": 10, "t": 40, "b": 10}, legend_title=None)
        st.plotly_chart(fig, width="stretch")
    with b:
        fig = px.line(ui.frame(service.runs()), x="as_of", y="health", markers=True, title="Health score")
        fig.update_layout(height=340, margin={"l": 10, "r": 10, "t": 40, "b": 10})
        st.plotly_chart(fig, width="stretch")
    st.caption("Backfilled daily runs replay the warehouse as it looked each day (rows filtered by load time), so "
               "the trend shows incidents arriving rather than a single static snapshot.")

with results_tab:
    f = st.columns(4)
    status = f[0].multiselect("Status", ["fail", "pass", "error"], default=["fail"])
    categories = f[1].multiselect("Category", sorted(results["category"].unique()))
    assets = f[2].multiselect("Dataset", sorted(results["asset"].unique()))
    severities = f[3].multiselect("Severity", ["critical", "high", "medium", "low"])
    view = results
    if status:
        view = view[view["status"].isin(status)]
    if categories:
        view = view[view["category"].isin(categories)]
    if assets:
        view = view[view["asset"].isin(assets)]
    if severities:
        view = view[view["severity"].isin(severities)]
    st.dataframe(view, hide_index=True, width="stretch", column_order=[
        "status", "rule_key", "asset", "category", "severity", "records_scanned", "records_failed", "failure_rate",
        "critical_columns", "downstream_count", "execution_ms", "owner", "issue_key"],
        column_config={"failure_rate": st.column_config.NumberColumn("Failure rate", format="percent"),
                       "critical_columns": st.column_config.ListColumn("Critical columns"),
                       "downstream_count": "Downstream", "execution_ms": "ms"})

    if view.empty:
        st.info("No results match the filters.")
    else:
        rule_key = st.selectbox("Inspect rule", view["rule_key"].tolist())
        row = view[view["rule_key"] == rule_key].iloc[0]
        with st.container(border=True):
            st.markdown(f"**{row['name']}** (`{row['rule_type']}` on `{row['asset']}`)")
            st.write(row["business_impact"])
            ui.badges((row["status"], {"fail": "red", "pass": "green", "error": "gray"}[row["status"]]),
                      (row["severity"], ui.SEVERITY_BADGE[row["severity"]]), (row["category"], "gray"))
            if row["issue_key"]:
                ui.page_link("issues", f"Investigate {row['issue_key']}", ":material/bug_report:",
                             issue=row["issue_key"])
            if row["error_message"]:
                st.error(row["error_message"])
            if row["sample_failures"]:
                st.markdown("Sample failing records")
                st.dataframe(ui.samples_frame(row["sample_failures"], list(row["column_names"])), hide_index=True,
                             width="stretch")
            history = ui.frame(service.rule_history(rule_key))
            fig = px.bar(history, x="as_of", y="records_failed", title="Failing records per run", height=240)
            fig.update_layout(margin={"l": 10, "r": 10, "t": 40, "b": 10})
            st.plotly_chart(fig, width="stretch")
            with st.expander("Executed SQL (failing rows)"):
                st.code(ui.run(lambda: service.rule_sql(rule_key)), language="sql")

with assets_tab:
    health = ui.frame(service.asset_health(int(run["run_id"])))
    fig = px.bar(health, x="health", y="asset", orientation="h", color="failing_rules",
                 color_continuous_scale="Reds", range_x=[0, 100], hover_data=["owner", "rules"])
    fig.update_layout(height=max(280, 28 * len(health)), margin={"l": 10, "r": 10, "t": 10, "b": 10},
                      yaxis={"autorange": "reversed", "title": None})
    st.plotly_chart(fig, width="stretch")

with rules_tab:
    catalog = ui.frame(service.rule_catalog())
    st.caption(f"{len(catalog)} rules defined declaratively in `config/quality_rules.yml`. Rules tagged only "
               "`baseline` belong to the first detector version and are kept so its evaluation stays reproducible.")
    st.dataframe(catalog, hide_index=True, width="stretch", column_order=[
        "rule_key", "category", "rule_type", "severity", "asset", "column_names", "rulesets", "supersedes",
        "description", "rationale"],
        column_config={"column_names": st.column_config.ListColumn("Columns"),
                       "rulesets": st.column_config.ListColumn("Rulesets")})
