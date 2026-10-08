"""DataTrust Streamlit entry point: `streamlit run app/streamlit_app.py`."""

from __future__ import annotations

import streamlit as st
import ui

from datatrust.logging_config import configure_logging

st.set_page_config(page_title="DataTrust", page_icon=":material/verified:", layout="wide")
configure_logging(ui.settings().log_level)

SECTIONS = {
    "Monitor": [
        ("overview", "views/overview.py", "Platform overview", ":material/monitor_heart:", True),
        ("quality", "views/quality.py", "Data quality", ":material/rule:", False),
        ("issues", "views/issues.py", "Issue investigation", ":material/bug_report:", False),
        ("priority", "views/prioritization.py", "Prioritization", ":material/sort:", False),
    ],
    "Explore": [
        ("catalog", "views/catalog.py", "Data catalog", ":material/search:", False),
        ("dataset", "views/dataset.py", "Dataset detail", ":material/table_view:", False),
        ("profiling", "views/profiling.py", "Profiling", ":material/query_stats:", False),
        ("lineage", "views/lineage.py", "Lineage", ":material/account_tree:", False),
        ("impact", "views/impact.py", "Impact analysis", ":material/crisis_alert:", False),
        ("glossary", "views/glossary.py", "Business glossary", ":material/menu_book:", False),
    ],
    "Assure": [
        ("evaluation", "views/evaluation.py", "Detector evaluation", ":material/fact_check:", False),
    ],
}

navigation: dict[str, list[st.Page]] = {}
for section, pages in SECTIONS.items():
    for key, path, title, icon, default in pages:
        page = st.Page(path, title=title, icon=icon, url_path=key, default=default)
        ui.PAGES[key] = page
        navigation.setdefault(section, []).append(page)

current = st.navigation(navigation)

with st.sidebar:
    state = ui.readiness()
    settings = ui.settings()
    st.caption(f"Copperleaf Coffee Co. warehouse · snapshot {settings.reference_date:%d %b %Y}")
    if not state.database:
        st.error("Database offline", icon=":material/cloud_off:")
    elif not state.ready:
        st.warning("Setup incomplete: " + ", ".join(state.missing_steps), icon=":material/construction:")
    else:
        st.caption(f"{state.counts['assets']} assets · {state.counts['quality_runs']} quality runs · "
                   f"{state.counts['evaluation_runs']} evaluation runs")
    if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
        ui.refresh()
        st.rerun()

current.run()
