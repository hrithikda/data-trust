"""Generate a coherent, reproducible operational dataset for Copperleaf Coffee Co.

The generator first builds *clean* data in which every business rule holds (money is
handled in integer cents so totals reconcile exactly). Defects are injected afterwards by
:mod:`datatrust.generator.defects`, which records ground truth for every corruption.
The same seed, scale and reference date always produce byte-identical output.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from datatrust.generator import company
from datatrust.generator.company import ONE_OFF_PRODUCTS, PLANS, PRODUCTS, Plan

logger = logging.getLogger(__name__)

Row = dict[str, Any]


def money(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(Decimal("0.01"))


def weighted(rng: random.Random, options: tuple[tuple[str, float], ...]) -> str:
    values, weights = zip(*options, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


@dataclass
class _Order:
    customer_id: str
    ordered_at: datetime
    currency: str
    tax_rate: float
    sales_channel: str
    lines: list[tuple[str, int, int]]  # (product_id, quantity, unit_price_cents)
    subscription_id: str | None = None
    campaign_id: str | None = None
    discount_rate: float = 0.0
    is_replacement: bool = False
    order_id: str = ""
    status: str = ""
    subtotal: int = 0
    discount: int = 0
    shipping: int = 0
    tax: int = 0
    total: int = 0


@dataclass
class GeneratedDataset:
    """Raw tables keyed by raw table name, plus ground truth for injected defects."""

    tables: dict[str, list[Row]]
    reference_date: date
    seed: int
    scale: float
    defects: list[dict[str, Any]] = field(default_factory=list)

    def row_counts(self) -> dict[str, int]:
        return {name: len(rows) for name, rows in self.tables.items()}


class CopperleafGenerator:
    """Builds clean operational data. Use :func:`generate_dataset` as the entry point."""

    def __init__(self, seed: int, scale: float, reference_date: date) -> None:
        self.rng = random.Random(seed)
        self.scale = scale
        self.reference_date = reference_date
        self.ref_ts = datetime.combine(reference_date, time(23, 59, 59))
        self.start = datetime.combine(company.BUSINESS_START, time(0, 0))
        self.products = {p.product_id: p for p in PRODUCTS}
        self.campaigns: list[Row] = []
        self.customers: list[Row] = []
        self.customer_country: dict[str, tuple[str, str, float]] = {}
        self.subscriptions: list[Row] = []
        self.orders: list[_Order] = []

    # ------------------------------------------------------------------ helpers
    def _random_ts(self, low: datetime, high: datetime) -> datetime:
        if high <= low:
            return low
        seconds = int((high - low).total_seconds())
        return low + timedelta(seconds=self.rng.randint(0, seconds))

    def _daytime(self, day: datetime) -> datetime:
        """Shift a timestamp to a plausible shopping hour on the same day."""
        moment = day.replace(hour=self.rng.randint(6, 22), minute=self.rng.randint(0, 59),
                             second=self.rng.randint(0, 59))
        return min(moment, self.ref_ts)

    def _unit_price(self, product_id: str, at: datetime) -> int:
        """List price at a point in time: prices rose ~6% on 2025-01-01."""
        price = self.products[product_id].price_cents
        if at < datetime(2025, 1, 1):
            price = int(round(price / 1.06 / 50.0)) * 50
        return price

    # --------------------------------------------------------------- entities
    def build_campaigns(self) -> None:
        cursor = date(2024, 1, 3)
        number = 1
        while cursor < self.reference_date:
            theme = company.CAMPAIGN_THEMES[(cursor.month - 1) % len(company.CAMPAIGN_THEMES)]
            channel = self.rng.choice(company.CAMPAIGN_CHANNELS)
            duration = self.rng.randint(14, 40)
            self.campaigns.append({
                "campaign_id": f"CMP{number:03d}",
                "campaign_name": f"{cursor.year} {theme} - {channel.replace('_', ' ').title()}",
                "channel": channel,
                "started_on": cursor,
                "ended_on": cursor + timedelta(days=duration),
                "budget_amount": money(self.rng.randint(20, 400) * 10000),
            })
            number += 1
            cursor += timedelta(days=self.rng.randint(18, 30))

    def build_customers(self) -> None:
        count = max(60, int(4000 * self.scale))
        span = (self.ref_ts - timedelta(days=1) - self.start).total_seconds()
        for i in range(count):
            # sqrt skew: the customer base grows over time
            signup = self.start + timedelta(seconds=int(span * self.rng.random() ** 0.5))
            country, currency, tax_rate = self._pick_country()
            first, last = self.rng.choice(company.FIRST_NAMES), self.rng.choice(company.LAST_NAMES)
            customer_id = f"C{100001 + i}"
            self.customer_country[customer_id] = (country, currency, tax_rate)
            self.customers.append({
                "customer_id": customer_id,
                "email": f"{first}.{last}{i % 97}@{self.rng.choice(company.EMAIL_DOMAINS)}".lower(),
                "first_name": first,
                "last_name": last,
                "country_code": country,
                "acquisition_channel": weighted(self.rng, company.ACQUISITION_CHANNELS),
                "customer_status": None,  # derived from activity once orders exist
                "signup_at": self._daytime(signup),
            })

    def _pick_country(self) -> tuple[str, str, float]:
        country = self.rng.choices(company.COUNTRIES, weights=[c[3] for c in company.COUNTRIES], k=1)[0]
        return country[0], country[1], country[2]

    def build_subscriptions(self) -> None:
        number = 1
        for customer in self.customers:
            if self.rng.random() > 0.36:
                continue
            plan: Plan = self.rng.choices(PLANS, weights=[p.weight for p in PLANS], k=1)[0]
            started = (customer["signup_at"] + timedelta(days=self.rng.randint(0, 20))).date()
            if started >= self.reference_date:
                continue
            status, ended, pause_on = "active", None, None
            roll = self.rng.random()
            if roll < 0.26:
                candidate = started + timedelta(days=self.rng.randint(45, 600))
                if candidate < self.reference_date:
                    status, ended = "cancelled", candidate
            elif roll < 0.33:
                status = "paused"
                pause_on = started + timedelta(days=self.rng.randint(30, 300))
                pause_on = min(pause_on, self.reference_date - timedelta(days=2))
            elif roll < 0.36:
                # prepaid gift subscription: active with a scheduled end date in the future
                ended = self.reference_date + timedelta(days=self.rng.randint(15, 240))
            subscription_id = f"SUB{number:06d}"
            number += 1
            self.subscriptions.append({
                "subscription_id": subscription_id,
                "customer_id": customer["customer_id"],
                "plan_code": plan.plan_code,
                "subscription_status": status,
                "billing_interval": plan.billing_interval,
                "price_per_cycle": money(plan.price_cents),
                "started_on": started,
                "ended_on": ended,
            })
            self._subscription_orders(customer["customer_id"], subscription_id, plan, started,
                                      ended if status == "cancelled" else pause_on)

    def _subscription_orders(self, customer_id: str, subscription_id: str, plan: Plan,
                             started: date, stop: date | None) -> None:
        _, currency, tax_rate = self.customer_country[customer_id]
        last_day = min(stop or self.reference_date, self.reference_date)
        cycle_day = started
        while cycle_day <= last_day:
            ordered_at = self._daytime(datetime.combine(cycle_day, time(0)))
            self.orders.append(_Order(
                customer_id=customer_id, ordered_at=ordered_at, currency=currency, tax_rate=tax_rate,
                sales_channel="subscription", subscription_id=subscription_id,
                lines=[(plan.product_id, 1, plan.price_cents)],
            ))
            if plan.billing_interval == "biweekly":
                cycle_day += timedelta(days=14)
            else:
                month = cycle_day.month % 12 + 1
                year = cycle_day.year + (1 if month == 1 else 0)
                cycle_day = cycle_day.replace(year=year, month=month, day=min(cycle_day.day, 28))

    def build_one_off_orders(self) -> None:
        for customer in self.customers:
            if self.rng.random() > 0.84:
                continue  # browsed, never purchased
            cursor = customer["signup_at"] + timedelta(minutes=self.rng.randint(5, 60 * 24 * 10))
            _, currency, tax_rate = self.customer_country[customer["customer_id"]]
            while cursor < self.ref_ts:
                self.orders.append(self._one_off_order(customer["customer_id"], cursor, currency, tax_rate))
                if self.rng.random() > 0.55:
                    break
                cursor += timedelta(days=self.rng.randint(7, 140), minutes=self.rng.randint(0, 900))

    def _one_off_order(self, customer_id: str, at: datetime, currency: str, tax_rate: float) -> _Order:
        ordered_at = self._daytime(at)
        line_count = self.rng.choices((1, 2, 3, 4), weights=(0.55, 0.28, 0.12, 0.05), k=1)[0]
        available = [p for p in ONE_OFF_PRODUCTS if p.launched_on <= ordered_at.date()]
        chosen = self.rng.sample(available, k=min(line_count, len(available)))
        lines = [
            (p.product_id, self.rng.choices((1, 2, 3), weights=(0.8, 0.15, 0.05), k=1)[0],
             self._unit_price(p.product_id, ordered_at))
            for p in chosen
        ]
        order = _Order(customer_id=customer_id, ordered_at=ordered_at, currency=currency, tax_rate=tax_rate,
                       sales_channel=self.rng.choices(("web", "app"), weights=(0.7, 0.3), k=1)[0], lines=lines)
        active = [c for c in self.campaigns if c["started_on"] <= ordered_at.date() <= c["ended_on"]]
        if active and self.rng.random() < 0.3:
            order.campaign_id = self.rng.choice(active)["campaign_id"]
            order.discount_rate = self.rng.choice((0.10, 0.15, 0.20))
        return order

    # ------------------------------------------------------------ finalisation
    def price_and_number_orders(self) -> None:
        self.orders.sort(key=lambda o: (o.ordered_at, o.customer_id))
        for number, order in enumerate(self.orders, start=1):
            order.order_id = f"SF{1000000 + number}"
            age = self.ref_ts - order.ordered_at
            order.subtotal = sum(qty * price for _, qty, price in order.lines)
            if order.sales_channel != "subscription" and age > timedelta(days=12) and self.rng.random() < 0.005:
                # support-issued replacement: fully discounted, no shipping or tax
                order.is_replacement = True
                order.sales_channel = "support_replacement"
                order.campaign_id = None
                order.discount = order.subtotal
            else:
                order.discount = int(round(order.subtotal * order.discount_rate))
            net = order.subtotal - order.discount
            if order.is_replacement or order.sales_channel == "subscription" or net >= 4000:
                order.shipping = 0
            else:
                order.shipping = {"USD": 595, "CAD": 795, "GBP": 495}[order.currency]
            order.tax = 0 if order.is_replacement else int(round(net * order.tax_rate))
            order.total = net + order.shipping + order.tax
            order.status = self._order_status(age)

    def _order_status(self, age: timedelta) -> str:
        roll = self.rng.random()
        if roll < 0.03:
            return "cancelled"
        if age < timedelta(days=1):
            return "placed"
        if age < timedelta(days=4):
            return "shipped"
        if roll < 0.05 and age > timedelta(days=25):
            return "returned"
        return "delivered"

    def build(self) -> dict[str, list[Row]]:
        self.build_campaigns()
        self.build_customers()
        self.build_subscriptions()
        self.build_one_off_orders()
        self.price_and_number_orders()
        orders, items = self._order_rows()
        payments, payment_cents = self._payments()
        refunds = self._refunds(payment_cents)
        tickets = self._tickets()
        self._derive_customer_status()
        tables = {
            "shopfront_customers": self.customers,
            "shopfront_products": [
                {"product_id": p.product_id, "sku": p.sku, "product_name": p.name, "category": p.category,
                 "unit_price": money(p.price_cents), "is_active": p.is_active, "launched_on": p.launched_on}
                for p in PRODUCTS
            ],
            "shopfront_orders": orders,
            "shopfront_order_items": items,
            "payrail_payments": payments,
            "payrail_refunds": refunds,
            "rebill_subscriptions": self.subscriptions,
            "deskline_tickets": tickets,
            "campaignhub_campaigns": self.campaigns,
        }
        self._stamp_load_times(tables)
        return tables

    def _stamp_load_times(self, tables: dict[str, list[Row]]) -> None:
        """Simulate ELT ingestion: each row lands shortly after the event that created it."""
        event_columns = {
            "shopfront_customers": "signup_at", "shopfront_products": "launched_on",
            "shopfront_orders": "ordered_at", "payrail_payments": "processed_at",
            "payrail_refunds": "refunded_at", "rebill_subscriptions": "started_on",
            "deskline_tickets": "opened_at", "campaignhub_campaigns": "started_on",
        }
        for table, column in event_columns.items():
            for row in tables[table]:
                event = row[column]
                if not isinstance(event, datetime):
                    event = datetime.combine(event, time(6, 0))
                row["_loaded_at"] = min(event + timedelta(minutes=self.rng.randint(2, 45)), self.ref_ts)
        order_loaded = {o["order_id"]: o["_loaded_at"] for o in tables["shopfront_orders"]}
        for item in tables["shopfront_order_items"]:
            item["_loaded_at"] = order_loaded[item["order_id"]]

    def _order_rows(self) -> tuple[list[Row], list[Row]]:
        orders: list[Row] = []
        items: list[Row] = []
        for order in self.orders:
            orders.append({
                "order_id": order.order_id, "customer_id": order.customer_id,
                "subscription_id": order.subscription_id, "campaign_id": order.campaign_id,
                "order_status": order.status, "sales_channel": order.sales_channel, "currency": order.currency,
                "subtotal_amount": money(order.subtotal), "discount_amount": money(order.discount),
                "shipping_amount": money(order.shipping), "tax_amount": money(order.tax),
                "total_amount": money(order.total), "ordered_at": order.ordered_at,
            })
            for line_no, (product_id, qty, price) in enumerate(order.lines, start=1):
                items.append({
                    "order_item_id": f"{order.order_id}-{line_no}", "order_id": order.order_id,
                    "product_id": product_id, "quantity": qty, "unit_price": money(price),
                    "line_amount": money(qty * price),
                })
        return orders, items

    def _payments(self) -> tuple[list[Row], dict[str, tuple[int, datetime, _Order]]]:
        rows: list[Row] = []
        succeeded: dict[str, tuple[int, datetime, _Order]] = {}
        number = 0

        def add(order: _Order, status: str, amount: int, at: datetime, method: str) -> str:
            nonlocal number
            number += 1
            payment_id = f"PAY{number:07d}"
            rows.append({
                "payment_id": payment_id, "order_id": order.order_id, "customer_id": order.customer_id,
                "payment_method": method, "payment_status": status, "amount": money(amount),
                "currency": order.currency, "processed_at": at,
            })
            if status == "succeeded":
                succeeded[payment_id] = (amount, at, order)
            return payment_id

        for order in self.orders:
            at = min(order.ordered_at + timedelta(seconds=self.rng.randint(20, 1200)), self.ref_ts)
            method = weighted(self.rng, company.PAYMENT_METHODS)
            if order.status == "cancelled":
                if self.rng.random() < 0.5:
                    add(order, "failed", order.total, at, method)
                continue
            if order.status == "placed" and self.rng.random() < 0.3:
                add(order, "pending", order.total, at, method)
                continue
            if self.rng.random() < 0.04:
                add(order, "failed", order.total, at, method)
                at = min(at + timedelta(minutes=self.rng.randint(2, 30)), self.ref_ts)
            if order.total > 4000 and order.sales_channel in {"web", "app"} and self.rng.random() < 0.06:
                gift = 2500 if order.total > 6000 else 1500
                add(order, "succeeded", gift, at, "gift_card")
                add(order, "succeeded", order.total - gift, min(at + timedelta(seconds=5), self.ref_ts), "card")
            else:
                add(order, "succeeded", order.total, at, "card" if order.is_replacement else method)
        return rows, succeeded

    def _refunds(self, payments: dict[str, tuple[int, datetime, _Order]]) -> list[Row]:
        rows: list[Row] = []
        refunded_orders: set[str] = set()

        def add(payment_id: str, amount: int, at: datetime, reason: str) -> None:
            rows.append({"refund_id": f"RF{len(rows) + 1:07d}", "payment_id": payment_id,
                         "refund_amount": money(amount), "refund_reason": reason, "refunded_at": at})

        for payment_id, (amount, processed_at, order) in payments.items():
            if amount == 0 or order.order_id in refunded_orders:
                continue
            first_at = processed_at + timedelta(days=self.rng.randint(3, 20), hours=self.rng.randint(0, 12))
            if first_at > self.ref_ts:
                continue
            if order.status == "returned":
                add(payment_id, amount, first_at, "order_returned")
                refunded_orders.add(order.order_id)
                continue
            if order.status != "delivered":
                continue
            roll = self.rng.random()
            if roll < 0.035:
                add(payment_id, int(amount * self.rng.uniform(0.15, 0.5)), first_at,
                    self.rng.choice(company.REFUND_REASONS[:4]))
                refunded_orders.add(order.order_id)
            elif roll < 0.045:
                second_at = first_at + timedelta(days=self.rng.randint(1, 6))
                if second_at > self.ref_ts:
                    continue
                add(payment_id, int(amount * self.rng.uniform(0.2, 0.4)), first_at, "damaged_in_transit")
                add(payment_id, int(amount * self.rng.uniform(0.1, 0.3)), second_at, "quality_complaint")
                refunded_orders.add(order.order_id)
        return rows

    def _tickets(self) -> list[Row]:
        rows: list[Row] = []

        def add(customer_id: str, order_id: str | None, opened_at: datetime) -> None:
            age = self.ref_ts - opened_at
            solved_at = opened_at + timedelta(minutes=self.rng.randint(45, 60 * 24 * 4))
            if age < timedelta(days=2) or solved_at > self.ref_ts:
                status, solved_at, csat = self.rng.choice(("open", "pending")), None, None
            else:
                status = "solved" if self.rng.random() < 0.7 else "closed"
                csat = self.rng.choices((1, 2, 3, 4, 5), weights=(0.06, 0.08, 0.16, 0.35, 0.35), k=1)[0] \
                    if self.rng.random() < 0.55 else None
            rows.append({
                "ticket_id": f"TCK{len(rows) + 1:06d}", "customer_id": customer_id, "order_id": order_id,
                "channel": weighted(self.rng, company.TICKET_CHANNELS),
                "priority": weighted(self.rng, company.TICKET_PRIORITIES), "ticket_status": status,
                "opened_at": opened_at, "solved_at": solved_at, "csat_score": csat,
            })

        events: list[tuple[datetime, str, str | None]] = []
        for order in self.orders:
            if self.rng.random() < 0.16 or order.is_replacement:
                opened = order.ordered_at + timedelta(hours=self.rng.randint(4, 240))
                if opened <= self.ref_ts:
                    events.append((opened, order.customer_id, order.order_id))
        for customer in self.customers:
            if self.rng.random() < 0.1:
                opened = self._random_ts(customer["signup_at"], self.ref_ts)
                events.append((opened, customer["customer_id"], None))
        for opened, customer_id, order_id in sorted(events, key=lambda e: (e[0], e[1])):
            add(customer_id, order_id, opened)
        return rows

    def _derive_customer_status(self) -> None:
        last_order: dict[str, datetime] = {}
        for order in self.orders:
            last_order[order.customer_id] = max(order.ordered_at, last_order.get(order.customer_id, order.ordered_at))
        active_subs = {s["customer_id"] for s in self.subscriptions if s["subscription_status"] == "active"}
        for customer in self.customers:
            cid = customer["customer_id"]
            last = last_order.get(cid)
            if cid in active_subs or (last and self.ref_ts - last <= timedelta(days=90)):
                status = "active"
            elif last is None:
                status = "active" if self.ref_ts - customer["signup_at"] <= timedelta(days=30) else "inactive"
            elif self.ref_ts - last <= timedelta(days=365):
                status = "inactive"
            else:
                status = "churned"
            customer["customer_status"] = status


def generate_dataset(seed: int, scale: float, reference_date: date, inject_defects: bool = True) -> GeneratedDataset:
    """Generate the full raw dataset, optionally with ground-truth defect injection."""
    from datatrust.generator.defects import DefectInjector

    tables = CopperleafGenerator(seed, scale, reference_date).build()
    dataset = GeneratedDataset(tables=tables, reference_date=reference_date, seed=seed, scale=scale)
    if inject_defects:
        dataset.defects = DefectInjector(random.Random(seed + 7919), reference_date).inject(tables)
    logger.info("Generated dataset: %s", dataset.row_counts())
    return dataset
