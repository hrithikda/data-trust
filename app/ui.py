"""Shared Streamlit helpers: service access, readiness guards, formatting and navigation.

Pages call services through the cached functions here; nothing in the UI issues SQL.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

import pandas as pd
import streamlit as st

from datatrust.config import Settings, get_settings
from datatrust.db import Database
from datatrust.errors import DataTrustError
from datatrust.services.catalog import CatalogService
from datatrust.services.evaluation import EvaluationService
from datatrust.services.glossary import GlossaryService
from datatrust.services.impact import ImpactService
from datatrust.services.issues import IssueService
from datatrust.services.lineage import LineageService
from datatrust.services.overview import OverviewService, Readiness, check_readiness
from datatrust.services.profiling import ProfilingService
from datatrust.services.quality import QualityViewService

T = TypeVar("T")
CACHE_TTL = 120

BAND_COLORS = {"P1": "#d64545", "P2": "#e8833a", "P3": "#e0b030", "P4": "#7b8794"}
SEVERITY_COLORS = {"critical": "#d64545", "high": "#e8833a", "medium": "#e0b030", "low": "#7b8794"}
STATUS_BADGE = {"open": "red", "investigating": "orange", "accepted": "violet", "resolved": "green"}
SEVERITY_BADGE = {"critical": "red", "high": "orange", "medium": "yellow", "low": "gray"}
LAYER_BADGE = {"raw": "gray", "staging": "blue", "intermediate": "violet", "mart": "green", "exposure": "orange"}
FRESHNESS_BADGE = {"fresh": "green", "stale": "red", "unknown": "gray"}

PAGES: dict[str, st.Page] = {}


# ---------------------------------------------------------------------- resources
@st.cache_resource
def settings() -> Settings:
    return get_settings()


@st.cache_resource
def db() -> Database:
    return Database(settings())


def overview() -> OverviewService:
    return OverviewService(db())


def catalog() -> CatalogService:
    return CatalogService(db())


def profiling() -> ProfilingService:
    return ProfilingService(db())


def quality() -> QualityViewService:
    return QualityViewService(db(), settings())


def issues() -> IssueService:
    return IssueService(db())


def glossary() -> GlossaryService:
    return GlossaryService(db())


def evaluation() -> EvaluationService:
    return EvaluationService(db(), settings().evaluation_dir)


@st.cache_resource(ttl=CACHE_TTL)
def lineage() -> LineageService:
    return LineageService(db())


@st.cache_resource(ttl=CACHE_TTL)
def impact() -> ImpactService:
    return ImpactService(db(), settings())


def refresh() -> None:
    """Drop cached data after a write (e.g. an issue status change)."""
    st.cache_data.clear()
    lineage.clear()
    impact.clear()


# ---------------------------------------------------------------------- guards
@st.cache_data(ttl=15, show_spinner=False)
def readiness() -> Readiness:
    return check_readiness(db())


SETUP_HINT = """
```bash
docker compose up -d          # or any PostgreSQL 15+ matching .env
make install                  # python -m venv .venv && pip install -e ".[dev]"
make pipeline                 # generate -> dbt -> ingest -> profile -> quality -> evaluate
```
"""


TABLE_STEPS = {"assets": "ingest", "profile_runs": "profile", "quality_runs": "quality backfill",
               "issues": "quality backfill", "evaluation_runs": "evaluate"}


def guard(*tables: str) -> Readiness:
    """Stop the page with an actionable message unless the required data exists."""
    state = readiness()
    if not state.database:
        st.error("**PostgreSQL is not reachable.** DataTrust needs its metadata database to show anything.")
        st.caption(state.message or "")
        st.markdown(SETUP_HINT)
        st.stop()
    if not state.metadata_schema:
        st.warning("**The DataTrust metadata schema has not been created yet.**")
        st.markdown(SETUP_HINT)
        st.stop()
    missing = [t for t in tables if not state.has(t)]
    if missing:
        steps = dict.fromkeys(TABLE_STEPS.get(t, "pipeline") for t in missing)
        st.info("This page needs data that has not been produced yet. Run "
                + ", ".join(f"`datatrust {s}`" for s in steps) + " (or `make pipeline` for everything).")
        st.stop()
    return state


def run(fn: Callable[[], T], empty_message: str | None = None) -> T:
    """Call a service; turn DataTrustError into a friendly message and stop the page."""
    try:
        return fn()
    except DataTrustError as exc:
        if empty_message:
            st.info(empty_message)
        st.warning(str(exc))
        st.stop()


# ---------------------------------------------------------------------- formatting
def frame(rows: list[dict[str, Any]] | list[Any], columns: list[str] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if columns is not None and not df.empty:
        df = df[[c for c in columns if c in df.columns]]
    return df


def pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}%}"


def num(value: Any) -> str:
    return "n/a" if value is None else f"{int(value):,}"


def health_label(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}"


def badges(*items: tuple[str, str]) -> None:
    """Render (label, color) badges on one line."""
    def clean(label: str) -> str:
        return label.replace("[", "(").replace("]", ")")

    st.markdown(" ".join(f":{color}-badge[{clean(label)}]" for label, color in items))


def md(text: str) -> str:
    """Escape underscores so dbt names like ``stg_payrail__payments`` are not read as bold markers."""
    return text.replace("_", "\\_")


def page_link(page: str, label: str, icon: str | None = None, **params: str) -> None:
    target = PAGES.get(page)
    if target is None:
        st.markdown(md(label))
        return
    st.page_link(target, label=md(label), icon=icon, query_params=params or None)


def samples_frame(samples: list[dict[str, Any]], rule_columns: list[str]) -> pd.DataFrame:
    """Failing-record samples with the rule's columns first, then identifiers, then the rest."""
    df = pd.DataFrame(samples)
    if df.empty:
        return df
    first = [c for c in rule_columns if c in df.columns]
    reference = sorted(c for c in df.columns if c.startswith("reference_") or c.startswith("child_"))
    ids = sorted(c for c in df.columns if c.endswith("_id") and c not in first)
    rest = [c for c in df.columns if c not in {*first, *reference, *ids}]
    return df[first + reference + ids + rest]


def query_choice(param: str, options: list[str], default: str | None = None) -> int:
    """Index of the option named by ``?param=...`` (deep links), else of ``default``."""
    wanted = st.query_params.get(param, default)
    return options.index(wanted) if wanted in options else 0


def sync_query(param: str, value: str) -> None:
    if st.query_params.get(param) != value:
        st.query_params[param] = value
