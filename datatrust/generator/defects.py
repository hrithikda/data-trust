"""Deterministic defect injection with ground truth.

Each defect models a plausible operational incident (a replayed webhook, a clock-skewed
payment worker, a lost cancellation event, ...). Defects are applied to the clean tables in
place, never overlap on the same record, and are returned as a list of ground-truth
records naming the rule expected to catch them.

Many defects are clustered in the last two weeks before the reference date so that the
quality backfill shows platform health declining, the way a real incident would.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

Row = dict[str, Any]


class DefectInjector:
    """Applies the defect catalogue to generated tables."""

    def __init__(self, rng: random.Random, reference_date: date) -> None:
        self.rng = rng
        self.ref_ts = datetime.combine(reference_date, time(23, 59, 59))
        self.touched_orders: set[str] = set()
        self.touched_payments: set[str] = set()
        self.defects: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- helpers
    def _window(self, days_from: int, days_to: int) -> tuple[datetime, datetime]:
        return self.ref_ts - timedelta(days=days_from), self.ref_ts - timedelta(days=days_to)

    def _pick(self, rows: list[Row], count: int, predicate: Callable[[Row], bool]) -> list[Row]:
        candidates = [r for r in rows if predicate(r)]
        count = min(count, max(1, len(candidates) // 3)) if candidates else 0
        return self.rng.sample(candidates, count) if count else []

    def _record(self, defect_type: str, table: str, rule: str, rows: list[Row], key: str,
                description: str, affected_rows: int | None = None) -> None:
        if not rows:
            return
        self.defects.append({
            "defect_type": defect_type,
            "table": table,
            "primary_rule": rule,
            "injected_records": len(rows),
            "expected_failing_rows": affected_rows if affected_rows is not None else len(rows),
            "record_ids": sorted(str(r[key]) for r in rows),
            "description": description,
        })

    def _order_ok(self, order: Row, low: datetime, high: datetime, channels: tuple[str, ...] = ("web", "app")) -> bool:
        return (order["order_id"] not in self.touched_orders and order["sales_channel"] in channels
                and low <= order["ordered_at"] <= high)

    # ---------------------------------------------------------------- catalogue
    def inject(self, tables: dict[str, list[Row]]) -> list[dict[str, Any]]:
        orders = tables["shopfront_orders"]
        items = tables["shopfront_order_items"]
        payments = tables["payrail_payments"]
        refunds = tables["payrail_refunds"]
        subscriptions = tables["rebill_subscriptions"]
        tickets = tables["deskline_tickets"]
        customers = tables["shopfront_customers"]
        orders_by_id = {o["order_id"]: o for o in orders}
        items_by_order: dict[str, list[Row]] = {}
        for item in items:
            items_by_order.setdefault(item["order_id"], []).append(item)
        payments_by_order: dict[str, list[Row]] = {}
        for payment in payments:
            payments_by_order.setdefault(payment["order_id"], []).append(payment)
        refunded_payments = {r["payment_id"] for r in refunds}

        def clean_paid_order(order: Row) -> bool:
            pays = payments_by_order.get(order["order_id"], [])
            return len(pays) == 1 and pays[0]["payment_status"] == "succeeded" and pays[0]["payment_id"] not in refunded_payments

        # 1. Orders carrying customer ids from a legacy namespace (marketplace import bug)
        low, high = self._window(6, 2)
        picked = self._pick(orders, 3, lambda o: self._order_ok(o, low, high) and clean_paid_order(o))
        for order in picked:
            order["customer_id"] = f"C{int(order['customer_id'][1:]) + 900000}"
            self.touched_orders.add(order["order_id"])
        self._record("orphaned_order_customer", "shopfront_orders", "orders_customer_exists", picked, "order_id",
                     "Marketplace import wrote customer ids from the legacy namespace; the customers do not exist.")

        # 2. Order sync replay duplicated orders (same order_id loaded twice)
        low, high = self._window(4, 3)
        picked = self._pick(orders, 6, lambda o: self._order_ok(o, low, high))
        for order in picked:
            duplicate = dict(order)
            duplicate["_loaded_at"] = order["_loaded_at"] + timedelta(hours=2)
            orders.append(duplicate)
            self.touched_orders.add(order["order_id"])
        self._record("duplicate_order_id", "shopfront_orders", "orders_order_id_unique", picked, "order_id",
                     "Shopfront order sync replayed a batch; orders were loaded twice.", 2 * len(picked))

        # 3. Order lines detached from their order during a schema migration
        picked = self._pick(items, 4, lambda i: i["order_id"] not in self.touched_orders
                            and len(items_by_order[i["order_id"]]) == 1
                            and orders_by_id[i["order_id"]]["sales_channel"] in ("web", "app"))
        for item in picked:
            self.touched_orders.add(item["order_id"])
            item["order_id"] = None
        self._record("missing_order_item_order_id", "shopfront_order_items", "order_items_order_id_not_null",
                     picked, "order_item_id", "Order lines lost their order_id during a schema migration.")

        # 4. Clock-skewed payment worker stamped captures before checkout
        low, high = self._window(9, 7)
        picked_orders = self._pick(orders, 20, lambda o: self._order_ok(o, low, high, ("web", "app", "subscription"))
                                   and clean_paid_order(o))
        picked = []
        for order in picked_orders:
            payment = payments_by_order[order["order_id"]][0]
            payment["processed_at"] = order["ordered_at"] - timedelta(minutes=self.rng.randint(60, 360))
            self.touched_orders.add(order["order_id"])
            self.touched_payments.add(payment["payment_id"])
            picked.append(payment)
        self._record("payment_before_order", "payrail_payments", "payments_after_order", picked, "payment_id",
                     "A PayRail worker with a skewed clock stamped captures hours before checkout.")

        # 5. Payments referencing orders that never reached Shopfront
        low, high = self._window(20, 1)
        picked = self._pick(payments, 4, lambda p: p["payment_id"] not in self.touched_payments
                            and p["order_id"] not in self.touched_orders and p["payment_status"] == "failed"
                            and low <= p["processed_at"] <= high)
        for payment in picked:
            payment["order_id"] = f"SF9{payment['order_id'][3:]}"
            self.touched_payments.add(payment["payment_id"])
        self._record("orphaned_payment_order", "payrail_payments", "payments_order_exists", picked, "payment_id",
                     "Abandoned-checkout payment attempts reference carts that never became orders.")

        # 6. Webhook retries duplicated payments
        low, high = self._window(15, 1)
        picked_orders = self._pick(orders, 8, lambda o: self._order_ok(o, low, high) and clean_paid_order(o))
        picked = []
        for order in picked_orders:
            payment = payments_by_order[order["order_id"]][0]
            duplicate = dict(payment)
            duplicate["_loaded_at"] = payment["_loaded_at"] + timedelta(minutes=7)
            payments.append(duplicate)
            self.touched_orders.add(order["order_id"])
            self.touched_payments.add(payment["payment_id"])
            picked.append(payment)
        self._record("duplicate_payment_id", "payrail_payments", "payments_payment_id_unique", picked, "payment_id",
                     "PayRail webhook retries inserted the same capture twice.", 2 * len(picked))

        # 7. Manual back-office entry with the wrong year
        low, high = self._window(10, 1)
        picked = self._pick(orders, 3, lambda o: self._order_ok(o, low, high))
        for order in picked:
            order["ordered_at"] = order["ordered_at"] + timedelta(days=365)
            self.touched_orders.add(order["order_id"])
        self._record("future_order_date", "shopfront_orders", "orders_ordered_at_not_future", picked, "order_id",
                     "Back-office manual orders were keyed with next year's date.")

        # 8. New 3PL integration emits an undocumented status
        low, high = self._window(6, 0)
        picked = self._pick(orders, 30, lambda o: self._order_ok(o, low, high, ("web", "app", "subscription"))
                            and o["order_status"] in ("shipped", "delivered"))
        for order in picked:
            order["order_status"] = "in_transit"
            self.touched_orders.add(order["order_id"])
        self._record("invalid_order_status", "shopfront_orders", "orders_status_accepted_values", picked, "order_id",
                     "The new fulfilment partner integration writes 'in_transit', which is not a Shopfront status.")

        # 9. Lost cancellation webhook: subscription still active after it ended
        picked = self._pick(subscriptions, 30, lambda s: s["subscription_status"] == "cancelled")
        for sub in picked:
            sub["subscription_status"] = "active"
        self._record("active_subscription_ended", "rebill_subscriptions", "subscriptions_active_not_ended", picked,
                     "subscription_id", "Rebill cancellation webhooks were dropped; status stayed 'active'.")

        # 10. Subscription end date before start (data entry on reactivation)
        picked = self._pick(subscriptions, 4, lambda s: s["subscription_status"] == "cancelled")
        for sub in picked:
            sub["ended_on"] = sub["started_on"] - timedelta(days=self.rng.randint(3, 40))
        self._record("subscription_end_before_start", "rebill_subscriptions", "subscriptions_end_after_start", picked,
                     "subscription_id", "Reactivated subscriptions kept the previous term's end date.")

        payment_by_id = {p["payment_id"]: p for p in payments}
        refunds_per_payment: dict[str, int] = {}
        for refund in refunds:
            refunds_per_payment[refund["payment_id"]] = refunds_per_payment.get(refund["payment_id"], 0) + 1

        # 11. Single refund larger than its payment (agent typed the wrong amount)
        picked = self._pick(refunds, 3, lambda r: r["payment_id"] not in self.touched_payments
                            and r["refund_reason"] != "order_returned"
                            and refunds_per_payment[r["payment_id"]] == 1)
        for refund in picked:
            refund["refund_amount"] = payment_by_id[refund["payment_id"]]["amount"] + Decimal(self.rng.randint(5, 20))
            self.touched_payments.add(refund["payment_id"])
        self._record("refund_exceeds_payment", "payrail_refunds", "refunds_not_exceeding_payment", picked,
                     "refund_id", "A support agent keyed a refund larger than the captured amount.")

        # 12. Cumulative partial refunds exceeding the payment (manual + automated returns flow)
        low, high = self._window(40, 5)
        picked_orders = self._pick(orders, 6, lambda o: self._order_ok(o, low, high) and clean_paid_order(o)
                                   and o["order_status"] == "delivered" and o["total_amount"] > 20)
        picked = []
        for order in picked_orders:
            payment = payments_by_order[order["order_id"]][0]
            for offset_days, share in ((3, self.rng.uniform(0.55, 0.7)), (5, self.rng.uniform(0.5, 0.65))):
                refunded_at = payment["processed_at"] + timedelta(days=offset_days)
                refunds.append({
                    "refund_id": f"RF9{len(refunds):06d}",
                    "payment_id": payment["payment_id"],
                    "refund_amount": (payment["amount"] * Decimal(str(round(share, 2)))).quantize(Decimal("0.01")),
                    "refund_reason": "damaged_in_transit",
                    "refunded_at": refunded_at,
                    "_loaded_at": refunded_at + timedelta(minutes=10),
                })
            self.touched_orders.add(order["order_id"])
            self.touched_payments.add(payment["payment_id"])
            picked.append(payment)
        self._record("cumulative_refunds_exceed_payment", "payrail_refunds", "refunds_cumulative_not_exceeding_payment",
                     picked, "payment_id",
                     "A manual goodwill refund and the automated returns flow both refunded the same capture.")

        # 13. PayRail account merge re-pointed captures to another customer
        low, high = self._window(12, 2)
        customer_ids = sorted(c["customer_id"] for c in customers)
        picked_orders = self._pick(orders, 9, lambda o: self._order_ok(o, low, high) and clean_paid_order(o))
        picked = []
        for order in picked_orders:
            payment = payments_by_order[order["order_id"]][0]
            other = self.rng.choice(customer_ids)
            while other == order["customer_id"]:
                other = self.rng.choice(customer_ids)
            payment["customer_id"] = other
            self.touched_orders.add(order["order_id"])
            self.touched_payments.add(payment["payment_id"])
            picked.append(payment)
        self._record("payment_customer_mismatch", "payrail_payments", "payments_customer_matches_order", picked,
                     "payment_id", "A PayRail customer-account merge re-pointed captures to a different customer.")

        # 14. Order line edited after checkout without repricing the order
        low, high = self._window(14, 1)
        picked_orders = self._pick(orders, 15, lambda o: self._order_ok(o, low, high))
        for order in picked_orders:
            item = items_by_order[order["order_id"]][0]
            item["quantity"] += 1
            item["line_amount"] = item["unit_price"] * item["quantity"]
            self.touched_orders.add(order["order_id"])
        self._record("order_subtotal_mismatch", "shopfront_orders", "orders_subtotal_matches_items", picked_orders,
                     "order_id", "Order lines were edited after checkout; the order subtotal was not recalculated.")

        # 15. Tax recalculated on the order without updating the total
        picked = self._pick(orders, 7, lambda o: self._order_ok(o, *self._window(30, 1)) and o["tax_amount"] > 0)
        for order in picked:
            order["tax_amount"] = order["tax_amount"] + Decimal("0.37")
            self.touched_orders.add(order["order_id"])
        self._record("order_total_formula_mismatch", "shopfront_orders", "orders_total_formula", picked, "order_id",
                     "A tax-engine retry rewrote tax_amount without recalculating total_amount.")

        # 16. Negative quantities from a returns adjustment written as an order line
        picked_orders = self._pick(orders, 4, lambda o: self._order_ok(o, *self._window(60, 1)))
        picked = []
        for order in picked_orders:
            item = items_by_order[order["order_id"]][0]
            item["quantity"] = -item["quantity"]
            item["line_amount"] = item["unit_price"] * item["quantity"]
            self.touched_orders.add(order["order_id"])
            picked.append(item)
        self._record("negative_quantity", "shopfront_order_items", "order_items_quantity_positive", picked,
                     "order_item_id", "A returns adjustment was written as an order line with negative quantity.")

        # 17. Reversals posted as succeeded captures with negative amounts
        picked_orders = self._pick(orders, 3, lambda o: self._order_ok(o, *self._window(45, 1)) and clean_paid_order(o))
        picked = []
        for order in picked_orders:
            payment = payments_by_order[order["order_id"]][0]
            payment["amount"] = -payment["amount"]
            self.touched_orders.add(order["order_id"])
            self.touched_payments.add(payment["payment_id"])
            picked.append(payment)
        self._record("negative_payment_amount", "payrail_payments", "payments_succeeded_amount_valid", picked,
                     "payment_id", "Chargeback reversals were posted as succeeded captures with negative amounts.")

        # 18. Payments missing their order reference
        picked = self._pick(payments, 3, lambda p: p["payment_id"] not in self.touched_payments
                            and p["order_id"] not in self.touched_orders and p["payment_status"] == "failed")
        for payment in picked:
            payment["order_id"] = None
            self.touched_payments.add(payment["payment_id"])
        self._record("missing_payment_order_id", "payrail_payments", "payments_order_id_not_null", picked,
                     "payment_id", "Card-verification attempts were captured without an order reference.")

        # 19. Tickets solved before they were opened (timezone conversion bug)
        picked = self._pick(tickets, 12, lambda t: t["solved_at"] is not None)
        for ticket in picked:
            ticket["solved_at"] = ticket["opened_at"] - timedelta(hours=self.rng.randint(1, 8))
        self._record("ticket_solved_before_opened", "deskline_tickets", "tickets_solved_after_opened", picked,
                     "ticket_id", "Deskline exported solved_at in local time instead of UTC.")
        skewed_tickets = {t["ticket_id"] for t in picked}

        # 20. New chat widget writes an unmapped channel value (large volume, low importance)
        low, _ = self._window(150, 0)
        candidates = [t for t in tickets if t["opened_at"] >= low and t["channel"] == "chat"
                      and t["ticket_id"] not in skewed_tickets]
        for ticket in candidates:
            ticket["channel"] = "chat_widget_v2"
        self._record("invalid_ticket_channel", "deskline_tickets", "tickets_channel_accepted_values", candidates,
                     "ticket_id", "The redesigned chat widget reports channel 'chat_widget_v2'.")

        # 21. Guest-checkout imports without email
        picked = self._pick(customers, 40, lambda c: True)
        for customer in picked:
            customer["email"] = None
        self._record("missing_customer_email", "shopfront_customers", "customers_email_not_null", picked,
                     "customer_id", "Guest-checkout imports created customers without an email address.")

        return self.defects
