-- Raw landing tables: one table per operational source object, loaded as-is.
-- Executed with search_path set to the raw schema. Tables are recreated on every load.

DROP TABLE IF EXISTS shopfront_customers, shopfront_products, shopfront_orders, shopfront_order_items,
    payrail_payments, payrail_refunds, rebill_subscriptions, deskline_tickets, campaignhub_campaigns CASCADE;

-- Shopfront: e-commerce storefront (customers, catalogue, orders)
CREATE TABLE shopfront_customers (
    customer_id         text,
    email               text,
    first_name          text,
    last_name           text,
    country_code        text,
    acquisition_channel text,
    customer_status     text,
    signup_at           timestamp,
    _loaded_at          timestamp NOT NULL
);

CREATE TABLE shopfront_products (
    product_id   text,
    sku          text,
    product_name text,
    category     text,
    unit_price   numeric(10, 2),
    is_active    boolean,
    launched_on  date,
    _loaded_at   timestamp NOT NULL
);

CREATE TABLE shopfront_orders (
    order_id        text,
    customer_id     text,
    subscription_id text,
    campaign_id     text,
    order_status    text,
    sales_channel   text,
    currency        text,
    subtotal_amount numeric(12, 2),
    discount_amount numeric(12, 2),
    shipping_amount numeric(12, 2),
    tax_amount      numeric(12, 2),
    total_amount    numeric(12, 2),
    ordered_at      timestamp,
    _loaded_at      timestamp NOT NULL
);

CREATE TABLE shopfront_order_items (
    order_item_id text,
    order_id      text,
    product_id    text,
    quantity      integer,
    unit_price    numeric(10, 2),
    line_amount   numeric(12, 2),
    _loaded_at    timestamp NOT NULL
);

-- PayRail: payment processor
CREATE TABLE payrail_payments (
    payment_id     text,
    order_id       text,
    customer_id    text,
    payment_method text,
    payment_status text,
    amount         numeric(12, 2),
    currency       text,
    processed_at   timestamp,
    _loaded_at     timestamp NOT NULL
);

CREATE TABLE payrail_refunds (
    refund_id     text,
    payment_id    text,
    refund_amount numeric(12, 2),
    refund_reason text,
    refunded_at   timestamp,
    _loaded_at    timestamp NOT NULL
);

-- Rebill: subscription billing
CREATE TABLE rebill_subscriptions (
    subscription_id     text,
    customer_id         text,
    plan_code           text,
    subscription_status text,
    billing_interval    text,
    price_per_cycle     numeric(10, 2),
    started_on          date,
    ended_on            date,
    _loaded_at          timestamp NOT NULL
);

-- Deskline: customer support ticketing
CREATE TABLE deskline_tickets (
    ticket_id     text,
    customer_id   text,
    order_id      text,
    channel       text,
    priority      text,
    ticket_status text,
    opened_at     timestamp,
    solved_at     timestamp,
    csat_score    integer,
    _loaded_at    timestamp NOT NULL
);

-- CampaignHub: marketing campaign management
CREATE TABLE campaignhub_campaigns (
    campaign_id   text,
    campaign_name text,
    channel       text,
    started_on    date,
    ended_on      date,
    budget_amount numeric(12, 2),
    _loaded_at    timestamp NOT NULL
);
