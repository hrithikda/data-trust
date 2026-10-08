-- Purchase history per customer.
select
    customer_id,
    min(ordered_at)                                                   as first_order_at,
    max(ordered_at)                                                   as last_order_at,
    count(*) filter (where is_revenue_order)                          as order_count,
    count(*) filter (where is_revenue_order and sales_channel = 'subscription') as subscription_order_count,
    sum(net_revenue)::numeric(14, 2)                                  as lifetime_net_revenue,
    sum(amount_refunded)::numeric(14, 2)                              as lifetime_refunds,
    (sum(net_revenue) / nullif(count(*) filter (where is_revenue_order), 0))::numeric(12, 2) as avg_order_value
from {{ ref('int_orders__enriched') }}
group by customer_id
