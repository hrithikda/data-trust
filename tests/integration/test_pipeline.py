"""End-to-end: build the whole platform in a throwaway database and check its claims.

1. Clean data (no injected defects) through dbt and both rule sets: the improved rules raise
   nothing; the baseline only flags the documented false-positive pattern.
2. ``datatrust pipeline`` on defective data: every injected defect is caught by the rule its
   ground truth names, issues are prioritised by impact, evaluation history is append-only.
3. Every Streamlit page renders against that database without exceptions.

Runs against database ``datatrust_test`` with a copy of the dbt project, so the demo database
and ``dbt/target`` are never touched. Takes about a minute.
"""

from __future__ import annotations

import argparse
import json
import shutil

import psycopg
import pytest
from psycopg import sql

from datatrust import cli
from datatrust.config import REPO_ROOT, Settings, get_settings
from datatrust.db import Database
from datatrust.quality.service import QualityService
from tests.conftest import EVALUATION_DIR

pytestmark = [pytest.mark.db, pytest.mark.integration]

TEST_DB = "datatrust_test"
PAGES = ["overview", "quality", "issues", "prioritization", "catalog", "dataset", "profiling", "lineage", "impact",
         "glossary", "evaluation"]


def failing_rules(db: Database, run_id: int) -> dict[str, int]:
    rows = db.fetch_all("SELECT r.rule_key, qr.records_failed FROM quality_results qr JOIN quality_rules r "
                        "USING (rule_id) WHERE qr.run_id = %s AND qr.status = 'fail'", (run_id,))
    return {r["rule_key"]: r["records_failed"] for r in rows}


def drop_database(settings: Settings) -> None:
    with psycopg.connect(settings.conninfo(dbname="postgres"), autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB)))


@pytest.fixture(scope="module")
def stack(db, tmp_path_factory):
    work = tmp_path_factory.mktemp("stack")
    shutil.copytree(REPO_ROOT / "dbt", work / "dbt", ignore=shutil.ignore_patterns("target", "logs"))
    (work / "evaluation").mkdir()
    for name in ("holdout_cases.yml", "regression_cases.yml", "failure_analysis.yml"):
        shutil.copy(EVALUATION_DIR / name, work / "evaluation" / name)
    settings = Settings(db_name=TEST_DB, dbt_project_dir=work / "dbt", evaluation_dir=work / "evaluation",
                        generated_data_dir=work / "generated", log_level="WARNING")
    drop_database(settings)
    test_db = Database(settings)
    state: dict = {"settings": settings, "db": test_db, "work": work}
    try:
        cli.cmd_init_db(argparse.Namespace(reset=True), settings, test_db)
        cli.cmd_generate(argparse.Namespace(seed=None, scale=None, clean=True), settings, test_db)
        cli.cmd_dbt(argparse.Namespace(), settings, test_db)
        cli.cmd_ingest(argparse.Namespace(), settings, test_db)
        quality = QualityService(test_db, settings)
        state["clean"] = {ruleset: failing_rules(test_db, quality.run(ruleset).run_id)
                          for ruleset in ("improved", "baseline")}

        cli.cmd_pipeline(argparse.Namespace(days=3, keep_history=False), settings, test_db)
        state["ground_truth"] = json.loads((work / "generated" / "defect_manifest.json").read_text())["defects"]
        yield state
    finally:
        drop_database(settings)


def test_clean_data_raises_no_improved_rule(stack):
    assert stack["clean"]["improved"] == {}


def test_clean_data_baseline_only_flags_the_documented_false_positive_pattern(stack):
    # Zero-total support replacements are legitimate (HO-059); the baseline's amount > 0 rule flags them.
    assert set(stack["clean"]["baseline"]) <= {"payments_succeeded_amount_positive"}


def test_every_injected_defect_is_caught_by_its_rule(stack):
    db = stack["db"]
    run_id = db.fetch_value("SELECT max(run_id) FROM quality_runs WHERE ruleset = 'improved'")
    failing = failing_rules(db, run_id)
    replaced_by = {r["supersedes"]: r["rule_key"] for r in db.fetch_all(
        "SELECT rule_key, supersedes FROM quality_rules WHERE supersedes IS NOT NULL")}
    expected: dict[str, int] = {}
    for defect in stack["ground_truth"]:
        rule = replaced_by.get(defect["primary_rule"], defect["primary_rule"])
        expected[rule] = expected.get(rule, 0) + defect["expected_failing_rows"]
    missed = {rule: rows for rule, rows in expected.items() if failing.get(rule, 0) < rows}
    assert missed == {}, f"rules that caught fewer rows than were injected: {missed} (got {failing})"


def test_issues_are_prioritised_by_impact_not_volume(stack):
    issues = stack["db"].fetch_all("SELECT issue_key, affected_records, priority_score, priority_band FROM issues "
                                   "WHERE status <> 'resolved' ORDER BY affected_records")
    smallest, largest = issues[0], issues[-1]
    assert largest["affected_records"] > 50 * smallest["affected_records"]
    assert smallest["priority_score"] > largest["priority_score"]
    assert {i["priority_band"] for i in issues} >= {"P1", "P4"}


def test_downstream_datasets_list_the_upstream_issues_reaching_them(stack):
    from datatrust.services.catalog import CatalogService

    inherited = CatalogService(stack["db"]).asset_detail("executive_kpi_dashboard")["inherited_issues"]
    titles = [i["title"] for i in inherited]
    assert any(t.startswith("Order ID is unique") for t in titles)
    assert not any(t.startswith("Ticket channel") for t in titles)  # support data never reaches the board deck
    assert all(i["path"][-1] == "executive_kpi_dashboard" for i in inherited)


def test_triage_and_history(stack):
    db = stack["db"]
    statuses = {r["status"] for r in db.fetch_all("SELECT status FROM issues")}
    assert {"open", "investigating", "accepted"} <= statuses
    assert db.fetch_value("SELECT count(*) FROM quality_runs WHERE trigger = 'backfill'") == 3
    assert db.fetch_value("SELECT count(*) FROM profile_runs WHERE status = 'succeeded'") == 1


def test_evaluation_runs_are_append_only(stack):
    db, settings = stack["db"], stack["settings"]
    cli.cmd_evaluate(argparse.Namespace(suite="holdout", detector="baseline", dry_run=False), settings, db)
    runs = db.fetch_all("SELECT true_positives, false_negatives, false_positives, true_negatives FROM evaluation_runs "
                        "WHERE suite = 'holdout' AND detector_version = 'baseline' ORDER BY eval_run_id")
    assert [tuple(r.values()) for r in runs] == [(28, 2, 1, 29), (28, 2, 1, 29)]
    snapshot = json.loads((stack["work"] / "evaluation" / "results" / "holdout_improved.json").read_text())
    assert snapshot["confusion_matrix"]["accuracy"] == 1.0


def test_every_page_renders(stack, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DATATRUST_DB_NAME", TEST_DB)
    get_settings.cache_clear()
    st.cache_resource.clear()
    st.cache_data.clear()
    try:
        failures = {}
        for page in PAGES:
            at = AppTest.from_file(str(REPO_ROOT / "app" / "streamlit_app.py"), default_timeout=90)
            at.run()
            if page != "overview":
                at.switch_page(f"views/{page}.py")
                at.run()
            problems = [str(e.value)[:300] for e in at.exception] + [e.value[:300] for e in at.error]
            if problems:
                failures[page] = problems
            assert at.title, f"{page} rendered no title"
        assert failures == {}
    finally:
        get_settings.cache_clear()
