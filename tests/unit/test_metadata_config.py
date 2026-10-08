"""Governance config, dbt artifact parsing and evaluation suite integrity."""

import json
import shutil

import pytest
import yaml

from datatrust.config import Settings
from datatrust.errors import ArtifactsMissingError, ConfigurationError
from datatrust.evaluation.cases import load_suite
from datatrust.metadata.dbt_artifacts import parse_artifacts
from datatrust.metadata.governance import load_governance
from tests.conftest import CONFIG_DIR, EVALUATION_DIR

REQUIRED_TEAMS = {"Commerce", "Payments", "Finance Analytics", "Growth Analytics", "Customer Experience",
                  "Customer Data", "Platform Engineering"}


def test_governance_defines_the_required_teams_and_resolves_references():
    governance = load_governance(CONFIG_DIR)
    assert {t.name for t in governance.teams} == REQUIRED_TEAMS
    assert {s.key for s in governance.source_systems} == {"shopfront", "payrail", "rebill", "deskline",
                                                         "campaignhub"}
    assert all(term.links for term in governance.terms)


def test_governance_rejects_unknown_owners(tmp_path):
    shutil.copytree(CONFIG_DIR / "governance", tmp_path / "governance")
    glossary = tmp_path / "governance" / "glossary.yml"
    content = yaml.safe_load(glossary.read_text())
    content["terms"][0]["owner"] = "marketing_wizards"
    glossary.write_text(yaml.safe_dump(content))
    with pytest.raises(ConfigurationError, match="unknown owner 'marketing_wizards'"):
        load_governance(tmp_path)


def test_parse_real_artifacts(dbt_target_dir):
    project = parse_artifacts(dbt_target_dir)
    layers = {a.layer for a in project.assets}
    assert layers == {"raw", "staging", "intermediate", "mart", "exposure"}
    orders = project.asset("stg_shopfront__orders")
    assert orders.owner == "commerce" and orders.criticality == "critical"
    assert any(c.is_critical and c.cde_category == "identifier" for c in orders.columns)
    source = project.asset("shopfront_orders")
    assert (source.freshness_column, source.freshness_sla_hours) == ("_loaded_at", 6)
    assert project.asset("shopfront_products").freshness_sla_hours is None
    exposure = project.asset("executive_kpi_dashboard")
    assert exposure.asset_type == "exposure" and exposure.audience == "executive"
    by_id = {a.unique_id: a.name for a in project.assets}
    edges = {(by_id[d.upstream], by_id[d.downstream]) for d in project.dependencies}
    assert ("stg_shopfront__orders", "int_orders__enriched") in edges
    assert project.tests, "dbt test results should be ingested alongside the manifest"


def test_invalid_meta_is_reported(dbt_target_dir, tmp_path):
    for name in ("manifest.json", "catalog.json"):
        shutil.copy(dbt_target_dir / name, tmp_path / name)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    node = next(n for n in manifest["nodes"].values() if n["name"] == "fct_orders")
    node["meta"]["criticality"] = "extreme"
    node["config"]["meta"] = node["meta"]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ConfigurationError, match="extreme"):
        parse_artifacts(tmp_path)
    with pytest.raises(ArtifactsMissingError):
        parse_artifacts(tmp_path / "nowhere")


def test_holdout_suite_is_balanced_and_labelled():
    suite = load_suite(EVALUATION_DIR / "holdout_cases.yml")
    assert len(suite.cases) == 60
    assert suite.label_counts() == {"defective": 30, "valid": 30}
    regression = load_suite(EVALUATION_DIR / "regression_cases.yml")
    assert not {c.id for c in suite.cases} & {c.id for c in regression.cases}


def test_failure_analysis_points_at_real_cases():
    holdout = {c.id: c for c in load_suite(EVALUATION_DIR / "holdout_cases.yml").cases}
    regression = {c.id for c in load_suite(EVALUATION_DIR / "regression_cases.yml").cases}
    analysis = yaml.safe_load((EVALUATION_DIR / "failure_analysis.yml").read_text())["cases"]
    assert {a["case_id"]: a["error"] for a in analysis} == {
        "HO-050": "false_negative", "HO-036": "false_negative", "HO-059": "false_positive"}
    for entry in analysis:
        expected = "defective" if entry["error"] == "false_negative" else "valid"
        assert holdout[entry["case_id"]].expected == expected
        assert set(entry["regression_cases"]) <= regression


def test_settings_validate_identifiers():
    with pytest.raises(ValueError, match="lowercase SQL identifier"):
        Settings(metadata_schema="datatrust; drop schema public")
    assert Settings(log_level="debug").log_level == "DEBUG"
    assert "datatrust" not in repr(Settings().db_password)  # SecretStr never prints the password
