-- Conformed customer dimension: profile, purchase history and subscription status.
with subscriptions as (
    select
        customer_id,
        count(*) filter (where is_active) as active_subscription_count,
        sum(mrr)::numeric(12, 2)          as mrr
    from {{ ref('int_subscriptions__mrr') }}
    group by customer_id
)

select
    c.customer_id,
    c.email,
    c.first_name,
    c.last_name,
    c.country_code,
    c.acquisition_channel,
    c.customer_status,
    c.signed_up_at,
    h.first_order_at,
    h.last_order_at,
    coalesce(h.order_count, 0)                              as order_count,
    coalesce(h.lifetime_net_revenue, 0)::numeric(14, 2)     as lifetime_net_revenue,
    coalesce(h.lifetime_refunds, 0)::numeric(14, 2)         as lifetime_refunds,
    h.avg_order_value,
    coalesce(s.active_subscription_count, 0)                as active_subscription_count,
    coalesce(s.mrr, 0)::numeric(12, 2)                      as mrr,
    coalesce(s.active_subscription_count, 0) > 0            as is_subscriber,
    ({{ reference_date() }} - h.last_order_at::date)        as days_since_last_order
from {{ ref('stg_shopfront__customers') }} c
left join {{ ref('int_customers__order_history') }} h on h.customer_id = c.customer_id
left join subscriptions s on s.customer_id = c.customer_id
