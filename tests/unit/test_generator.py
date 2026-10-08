from collections import Counter
from datetime import date

import pytest

from datatrust.generator.generate import generate_dataset
from datatrust.quality.rules import load_rules
from tests.conftest import CONFIG_DIR

REF = date(2026, 9, 30)


@pytest.fixture(scope="module")
def dataset():
    return generate_dataset(seed=11, scale=0.3, reference_date=REF)


def test_generation_is_deterministic(dataset):
    again = generate_dataset(seed=11, scale=0.3, reference_date=REF)
    assert again.tables == dataset.tables
    assert again.defects == dataset.defects
    other = generate_dataset(seed=12, scale=0.3, reference_date=REF)
    assert other.tables["shopfront_orders"] != dataset.tables["shopfront_orders"]


def test_every_source_system_produces_rows(dataset):
    counts = dataset.row_counts()
    assert set(counts) == {"shopfront_customers", "shopfront_products", "shopfront_orders", "shopfront_order_items",
                           "payrail_payments", "payrail_refunds", "rebill_subscriptions", "deskline_tickets",
                           "campaignhub_campaigns"}
    assert all(n > 0 for n in counts.values())


def test_clean_data_respects_the_basic_invariants():
    clean = generate_dataset(seed=11, scale=0.3, reference_date=REF, inject_defects=False)
    assert clean.defects == []
    tables = clean.tables
    for name, rows in tables.items():
        for row in rows:
            for column, value in row.items():
                if column.endswith("_at") and value is not None:
                    assert value.date() <= REF, (name, column, value)
    orders = {o["order_id"]: o for o in tables["shopfront_orders"]}
    assert len(orders) == len(tables["shopfront_orders"])
    customers = {c["customer_id"] for c in tables["shopfront_customers"]}
    assert all(o["customer_id"] in customers for o in orders.values())
    assert all(p["order_id"] in orders and p["customer_id"] == orders[p["order_id"]]["customer_id"]
               for p in tables["payrail_payments"])


def test_ground_truth_names_real_rules_and_records(dataset):
    rule_keys = {r.key for r in load_rules(CONFIG_DIR / "quality_rules.yml")}
    assert len(dataset.defects) >= 15
    for defect in dataset.defects:
        assert defect["primary_rule"] in rule_keys, defect
        assert defect["injected_records"] == len(defect["record_ids"]) > 0
    # defects never overlap on the same record of the same table
    owners = Counter((d["table"], rid) for d in dataset.defects for rid in d["record_ids"])
    assert max(owners.values()) == 1
