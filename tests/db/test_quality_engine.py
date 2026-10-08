"""Every rule type executed against hand-built tables in a scratch schema."""

from datetime import datetime

import pytest
from psycopg import sql

from datatrust.quality.engine import QualityEngine
from datatrust.quality.resolver import SchemaResolver
from datatrust.quality.rules import QualityRule

pytestmark = pytest.mark.db

AS_OF = datetime(2026, 9, 30, 23, 59, 59)

DDL = """
CREATE TABLE {s}.orders (order_id text, customer_id text, order_status text, total_amount numeric,
                         ordered_at timestamp, loaded_at timestamp);
CREATE TABLE {s}.customers (customer_id text);
CREATE TABLE {s}.payments (payment_id text, order_id text, customer_id text, amount numeric, processed_at timestamp);
CREATE TABLE {s}.refunds (refund_id text, payment_id text, refund_amount numeric);
INSERT INTO {s}.customers VALUES ('C1'), ('C2');
INSERT INTO {s}.orders VALUES
  ('O1', 'C1', 'delivered', 40.00, '2026-09-01 10:00', '2026-09-01 11:00'),
  ('O2', 'C2', 'delivered', 25.00, '2026-09-02 10:00', '2026-09-02 11:00'),
  ('O2', 'C2', 'delivered', 25.00, '2026-09-02 10:00', '2026-09-29 11:00'),   -- replayed duplicate
  ('O3', 'C9', 'in_transit', 10.00, '2026-10-04 10:00', '2026-09-30 11:00'),  -- orphan, bad status, future
  (NULL, 'C1', 'delivered', -5.00, '2026-09-03 10:00', '2026-09-03 11:00');   -- no id, negative
INSERT INTO {s}.payments VALUES
  ('P1', 'O1', 'C1', 40.00, '2026-09-01 10:05'),
  ('P2', 'O2', 'C1', 25.00, '2026-09-01 09:00');                              -- wrong customer, before order
INSERT INTO {s}.refunds VALUES ('R1', 'P1', 30.00), ('R2', 'P1', 15.00), ('R3', 'P2', 5.00);
"""


@pytest.fixture
def engine(db, scratch_schema):
    db.execute(sql.SQL(DDL).format(s=sql.Identifier(scratch_schema)))
    return QualityEngine(db, SchemaResolver(scratch_schema, loaded_at="loaded_at"), sample_size=3)


def make(type_: str, asset: str = "orders", columns=(), params=None, where=None, category="business_rule"):
    return QualityRule.model_validate({
        "key": f"t_{type_}", "name": type_, "category": category, "type": type_, "asset": asset,
        "columns": list(columns), "severity": "high", "business_impact": "test", "rulesets": ["improved"],
        "params": params or {}, "where": where})


def run(engine, rule, **kwargs):
    (outcome,) = engine.execute([rule], AS_OF, **kwargs)
    assert outcome.status != "error", outcome.error
    return outcome


@pytest.mark.parametrize(("rule", "failing"), [
    (make("not_null", columns=["order_id"]), 1),
    (make("unique", columns=["order_id"]), 2),  # both copies of O2; NULL ids are not duplicates
    (make("relationship", params={"column": "customer_id", "to_asset": "customers", "to_column": "customer_id"}), 1),
    (make("accepted_values", params={"column": "order_status", "values": ["delivered", "cancelled"]}), 1),
    (make("range", params={"column": "total_amount", "min": 0}), 1),
    (make("range", params={"column": "total_amount", "min": 0, "min_exclusive": True},
          where="order_status = 'delivered'"), 1),
    (make("not_future", params={"column": "ordered_at"}), 1),
    (make("chronology", asset="payments", params={
        "later_column": "processed_at", "earlier_column": "ordered_at", "reference_asset": "orders",
        "join": {"order_id": "order_id"}}), 1),  # P2 joins two O2 rows but is counted once
    (make("row_condition", params={"invalid_when": "p.order_status = 'in_transit' AND p.ordered_at > {reference_ts}"}),
     1),
    (make("join_condition", asset="payments", params={
        "reference_asset": "orders", "join": {"order_id": "order_id"},
        "invalid_when": "p.customer_id IS DISTINCT FROM r.customer_id", "context_columns": ["customer_id"]}), 1),
    (make("aggregate_reconciliation", asset="payments", params={
        "child_asset": "refunds", "join": {"payment_id": "payment_id"}, "primary_expression": "p.amount",
        "child_aggregate": "sum(c.refund_amount)", "comparison": "child_lte_primary"}), 1),  # 45 > 40 on P1
])
def test_each_check_type_finds_exactly_the_bad_rows(engine, rule, failing):
    outcome = run(engine, rule)
    assert outcome.records_failed == failing
    assert outcome.status == "fail"
    assert len(outcome.samples) == failing
    assert all("_dt_row" not in s for s in outcome.samples)


def test_samples_carry_context_from_the_joined_asset(engine):
    rule = make("join_condition", asset="payments", params={
        "reference_asset": "orders", "join": {"order_id": "order_id"},
        "invalid_when": "p.customer_id IS DISTINCT FROM r.customer_id", "context_columns": ["customer_id"]})
    (sample,) = run(engine, rule).samples
    assert (sample["payment_id"], sample["customer_id"], sample["reference_customer_id"]) == ("P2", "C1", "C2")


def test_scanned_rows_respect_scope_and_passing_rules_have_no_samples(engine):
    outcome = run(engine, make("not_null", columns=["customer_id"], where="order_status = 'delivered'"))
    assert (outcome.status, outcome.records_scanned, outcome.records_failed, outcome.samples) == ("pass", 4, 0, [])


def test_backfill_only_sees_rows_loaded_by_the_cutoff(engine):
    rule = make("unique", columns=["order_id"])
    assert run(engine, rule).records_failed == 2
    (earlier,) = engine.execute([rule], datetime(2026, 9, 20, 23, 59, 59), filter_by_load_time=True)
    assert earlier.records_failed == 0  # the replayed duplicate was loaded on 29 Sep
    assert earlier.records_scanned == 3


def test_a_broken_rule_is_isolated_from_the_others(engine):
    broken = make("relationship", params={"column": "customer_id", "to_asset": "ghosts", "to_column": "id"})
    good = make("not_null", columns=["order_id"])
    outcomes = engine.execute([broken, good], AS_OF)
    assert outcomes[0].status == "error" and "ghosts" in outcomes[0].error
    assert outcomes[1].status == "fail" and outcomes[1].records_failed == 1
