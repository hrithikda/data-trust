from pathlib import Path

import pytest
import yaml
from psycopg import sql

from datatrust.errors import ConfigurationError, RuleExecutionError
from datatrust.quality.checks import CHECKS, CompileContext
from datatrust.quality.engine import QualityEngine
from datatrust.quality.resolver import SchemaResolver
from datatrust.quality.rules import CATEGORIES, QualityRule, load_rules, validate_rules_against_metadata
from tests.conftest import CONFIG_DIR

RULES_FILE = CONFIG_DIR / "quality_rules.yml"


def rule(**overrides) -> QualityRule:
    base = {"key": "r", "name": "R", "category": "invalid_numeric", "type": "range", "asset": "stg_payments",
            "severity": "high", "business_impact": "x", "rulesets": ["baseline", "improved"],
            "params": {"column": "amount", "min": 0}}
    return QualityRule.model_validate({**base, **overrides})


def compiled(r: QualityRule) -> str:
    engine = QualityEngine(db=None, resolver=SchemaResolver("sandbox"))  # compile only, never executed
    scanned, failing = engine.compile(r)
    return failing.as_string(None)


def test_rule_file_covers_every_category_and_both_rulesets():
    rules = load_rules(RULES_FILE)
    assert {r.category for r in rules} == set(CATEGORIES)
    baseline, improved = load_rules(RULES_FILE, "baseline"), load_rules(RULES_FILE, "improved")
    assert baseline and improved
    assert {r.type for r in rules} == set(CHECKS)


def test_improved_rules_replace_exactly_the_three_baseline_weaknesses():
    rules = {r.key: r for r in load_rules(RULES_FILE)}
    superseding = {r.key: r.supersedes for r in rules.values() if r.supersedes}
    assert superseding == {
        "refunds_cumulative_not_exceeding_payment": "refunds_not_exceeding_payment",
        "payments_succeeded_amount_valid": "payments_succeeded_amount_positive",
    }
    for key in superseding.values():
        assert rules[key].rulesets == ["baseline"]
    added = [r for r in rules.values() if r.rulesets == ["improved"] and not r.supersedes]
    assert [r.key for r in added] == ["payments_customer_matches_order"]
    for key, case in (("refunds_cumulative_not_exceeding_payment", "HO-050"),
                      ("payments_customer_matches_order", "HO-036"),
                      ("payments_succeeded_amount_valid", "HO-059")):
        assert case in rules[key].rationale


@pytest.mark.parametrize(("overrides", "message"), [
    ({"type": "regex"}, "unknown rule type"),
    ({"category": "vibes"}, "unknown category"),
    ({"columns": ["Amount; DROP"]}, "invalid column identifiers"),
    ({"params": {"column": "amount", "min": 0, "colour": "red"}}, "Extra inputs"),
    ({"params": {"column": "amount\"; --"}}, "valid identifier"),
    ({"rulesets": []}, "at least 1"),
])
def test_invalid_rules_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        rule(**overrides)


def test_loader_reports_duplicates_and_dangling_supersedes(tmp_path: Path):
    raw = rule().model_dump()
    path = tmp_path / "rules.yml"
    path.write_text(yaml.safe_dump({"rules": [raw, raw, {**raw, "key": "s", "supersedes": "ghost"}]}))
    with pytest.raises(ConfigurationError) as excinfo:
        load_rules(path)
    assert "duplicate rule key 'r'" in str(excinfo.value)
    assert "supersedes unknown rule 'ghost'" in str(excinfo.value)
    with pytest.raises(ConfigurationError, match="not found"):
        load_rules(tmp_path / "missing.yml")


def test_rules_are_validated_against_warehouse_metadata():
    bad = rule(columns=["amount", "colour"],
               type="relationship", params={"column": "order_id", "to_asset": "stg_ghosts", "to_column": "id"})
    with pytest.raises(ConfigurationError) as excinfo:
        validate_rules_against_metadata([bad], {"stg_payments": {"amount", "order_id"}})
    assert "unknown asset 'stg_ghosts'" in str(excinfo.value)
    assert "unknown column 'stg_payments.colour'" in str(excinfo.value)


def test_compiled_sql_quotes_identifiers_and_binds_constants():
    text = compiled(rule(type="accepted_values", category="invalid_category",
                         params={"column": "status", "values": ["paid", "o'brien"]}))
    assert '"sandbox"."stg_payments"' in text
    assert "NOT IN ('paid', 'o''brien')" in text
    text = compiled(rule(type="not_future", category="invalid_date", params={"column": "paid_at"}))
    assert "%(reference_ts)s" in text  # the cutoff is a bound parameter, not interpolated


def test_join_checks_count_each_primary_row_once():
    text = compiled(rule(type="join_condition", category="reconciliation", params={
        "reference_asset": "stg_orders", "join": {"order_id": "order_id"},
        "invalid_when": "p.customer_id IS DISTINCT FROM r.customer_id"}))
    assert 'SELECT DISTINCT ON (p."_dt_row")' in text
    assert 'JOIN "sandbox"."stg_orders" r ON "r"."order_id" = "p"."order_id"' in text


def test_expressions_expand_tokens_and_reject_statements():
    ctx = CompileContext(SchemaResolver("s"), sql.SQL(""))
    text = ctx.expression("p.ts > {reference_ts} AND p.id IN (SELECT id FROM {ref:stg_x}) AND p.pct LIKE '5%'")
    assert text.as_string(None) == ("p.ts > %(reference_ts)s AND p.id IN (SELECT id FROM \"s\".\"stg_x\") "
                                    "AND p.pct LIKE '5%%'")
    for bad in ("1=1; DROP TABLE x", "amount > 0 -- comment", "x IN (DELETE FROM y)"):
        with pytest.raises(RuleExecutionError):
            ctx.expression(bad)
