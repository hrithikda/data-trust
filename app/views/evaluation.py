"""Detector evaluation: how reliable is the quality system itself?"""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import ui

st.title("Detector evaluation")
ui.guard("evaluation_runs")
service = ui.evaluation()

st.caption("60 held-out cases (30 defective, 30 valid) authored separately from rule development. Each case is "
           "loaded alone into a sandbox schema with shared reference data; a case is predicted defective if any "
           "rule of the detector fails. Metrics are computed from the stored predictions.")

suite = st.segmented_control("Suite", ["holdout", "regression"], default="holdout")
baseline, improved = service.latest(suite, "baseline"), service.latest(suite, "improved")
if baseline is None:
    st.info(f"No baseline evaluation for '{suite}'. Run `datatrust evaluate`.")
    st.stop()

METRICS = ["accuracy", "precision", "recall", "f1_score", "specificity"]


def matrix_figure(run: dict, title: str) -> go.Figure:
    z = [[run["true_positives"], run["false_negatives"]], [run["false_positives"], run["true_negatives"]]]
    labels = [[f"TP<br>{z[0][0]}", f"FN<br>{z[0][1]}"], [f"FP<br>{z[1][0]}", f"TN<br>{z[1][1]}"]]
    fig = go.Figure(go.Heatmap(z=[[z[0][0], z[0][1]], [z[1][0], z[1][1]]], x=["Predicted defective", "Predicted valid"],
                               y=["Actually defective", "Actually valid"], text=labels, texttemplate="%{text}",
                               colorscale=[[0, "#f5f7fa"], [1, "#3d7bd9"]], showscale=False))
    fig.update_layout(title=title, height=300, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      yaxis={"autorange": "reversed"})
    return fig


cols = st.columns(2)
for col, run, name in ((cols[0], baseline, "Baseline"), (cols[1], improved, "Improved")):
    with col:
        if run is None:
            st.info(f"No {name.lower()} run yet.")
            continue
        st.plotly_chart(matrix_figure(run, f"{name} detector · run {run['eval_run_id']}"), width="stretch")
        m = st.columns(5)
        for i, metric in enumerate(METRICS):
            delta = None
            if name == "Improved" and run[metric] is not None and baseline[metric] is not None:
                delta = f"{run[metric] - baseline[metric]:+.3f}"
            m[i].metric(metric.replace("_score", "").title(), f"{run[metric]:.3f}" if run[metric] is not None
                        else "n/a", delta=delta)

if improved is not None:
    comparison = ui.frame([{"detector": d, "metric": m.replace("_score", ""), "value": r[m]}
                           for d, r in (("baseline", baseline), ("improved", improved)) for m in METRICS])
    fig = px.bar(comparison, x="metric", y="value", color="detector", barmode="group", range_y=[0, 1.05],
                 color_discrete_map={"baseline": "#9aa5b1", "improved": "#2f9e63"}, text_auto=".3f")
    fig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10}, legend_title=None)
    st.plotly_chart(fig, width="stretch")

if suite == "holdout":
    st.subheader("Failure analysis of the baseline")
    for error in ui.run(service.failure_analysis):
        analysis = error.get("analysis") or {}
        kind = "missed defect (false negative)" if error["outcome"] == "FN" else "false alarm (false positive)"
        with st.container(border=True):
            st.markdown(f"#### {error['case_id']} · {error['title']}")
            ui.badges((kind, "red" if error["outcome"] == "FN" else "orange"),
                      (f"improved: {error['improved_outcome'] or 'not run'}",
                       "green" if error["improved_outcome"] in ("TP", "TN") else "gray"))
            st.write(error["description"])
            if analysis:
                st.markdown(f"**Pattern:** {analysis['pattern']}")
                st.markdown(f"**Why the baseline got it wrong:** {analysis['why']}")
                st.markdown(f"**Faulty assumption:** {analysis['assumption']}")
                st.markdown(f"**Improvement:** {analysis['improvement']}")
                st.caption("Regression coverage: " + ", ".join(analysis.get("regression_cases", [])))
            if error["triggered_rules"]:
                st.caption("Baseline fired: " + ", ".join(f"`{r}`" for r in error["triggered_rules"]))
            if error["improved_triggered_rules"]:
                st.caption("Improved fires: " + ", ".join(f"`{r}`" for r in error["improved_triggered_rules"]))
            with st.expander("Case records"):
                for table, rows in error["payload"].items():
                    st.markdown(f"`{table}`")
                    st.dataframe(ui.frame(rows), hide_index=True, width="stretch")
else:
    st.subheader("Regression coverage")
    st.caption("Variants of the three baseline errors. The baseline is expected to fail some; the improved detector "
               "must pass all.")
    st.dataframe(ui.frame(service.regression_coverage()), hide_index=True, width="stretch", column_order=[
        "case_id", "title", "expected_label", "baseline_outcome", "outcome", "triggered_rules"],
        column_config={"outcome": "Improved outcome", "triggered_rules": st.column_config.ListColumn("Improved fires")})

st.subheader("All predictions")
detector = st.segmented_control("Detector", ["baseline", "improved"], default="baseline", key="pred_detector")
run = baseline if detector == "baseline" else improved
if run is not None:
    preds = ui.frame(service.predictions(run["eval_run_id"]))
    outcome_filter = st.multiselect("Outcome", ["TP", "FN", "FP", "TN"])
    if outcome_filter:
        preds = preds[preds["outcome"].isin(outcome_filter)]
    st.dataframe(preds, hide_index=True, width="stretch", column_order=[
        "case_id", "outcome", "expected_label", "predicted_label", "defect_category", "title", "triggered_rules"],
        column_config={"triggered_rules": st.column_config.ListColumn("Triggered rules")})

with st.expander("Evaluation run history (append-only)"):
    st.caption("Every run is kept. Re-running the baseline adds a new row rather than replacing earlier results; "
               "`cases_hash` shows which version of the case file each run used.")
    st.dataframe(ui.frame(service.runs()), hide_index=True, width="stretch")
