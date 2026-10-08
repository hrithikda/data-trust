-- Payment fact: one row per PayRail attempt with refunds and order context.
select
    p.payment_id,
    p.order_id,
    p.customer_id,
    p.payment_method,
    p.payment_status,
    p.amount,
    p.currency,
    p.processed_at,
    p.refund_count,
    p.refunded_amount,
    p.net_amount,
    o.order_status,
    o.ordered_at,
    round(extract(epoch from (p.processed_at - o.ordered_at)) / 60.0, 1) as minutes_from_checkout
from {{ ref('int_payments__with_refunds') }} p
left join {{ ref('stg_shopfront__orders') }} o on o.order_id = p.order_id
