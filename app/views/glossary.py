"""Business glossary: business language linked to the datasets and fields that implement it."""

from __future__ import annotations

import streamlit as st
import ui

st.title("Business glossary")
ui.guard("assets")
service = ui.glossary()
cols = st.columns([3, 2, 2])
query = cols[0].text_input("Search terms", placeholder="e.g. revenue, churn, refund")
domains = cols[1].multiselect("Domain", service.domains())
statuses = cols[2].multiselect("Status", ["approved", "draft", "deprecated"])
terms = ui.run(lambda: service.terms(query, domains, statuses))
st.caption(f"{len(terms)} term(s)")
if not terms:
    st.info("No terms match.")
STATUS_COLOR = {"approved": "green", "draft": "orange", "deprecated": "gray"}
for term in terms:
    with st.expander(f"**{term['name']}** · {term['domain']} · {term['link_count']} link(s)",
                     expanded=bool(query)):
        ui.badges((term["status"], STATUS_COLOR[term["status"]]), (f"owner: {term['owner']}", "blue"),
                  (f"steward: {term['steward']}", "gray"))
        st.write(term["definition"])
        if term["calculation"]:
            st.markdown(f"**Calculation:** {term['calculation']}")
        if term["synonyms"]:
            st.caption("Also known as: " + ", ".join(term["synonyms"]))
        if term["related_term_keys"]:
            st.caption("Related: " + ", ".join(term["related_term_keys"]))
        links = service.links(term["term_key"])
        for link in links:
            label = f"{link['asset']}" + (f" · `{link['column_name']}`" if link["column_name"] else "")
            ui.page_link("dataset", label + (" ★" if link["is_critical"] else ""), ":material/link:",
                         asset=link["asset"])
