-- Order header joined to its lines and cash. Defines revenue at order grain.
select
    o.order_id,
    o.customer_id,
    o.subscription_id,
    o.campaign_id,
    o.order_status,
    o.sales_channel,
    o.currency,
    o.ordered_at,
    o.ordered_at::date                                           as order_date,
    o.subtotal_amount,
    o.discount_amount,
    o.shipping_amount,
    o.tax_amount,
    o.total_amount,
    coalesce(i.line_count, 0)                                    as line_count,
    coalesce(i.units, 0)                                         as units,
    i.categories,
    coalesce(p.successful_payment_count, 0)                      as successful_payment_count,
    coalesce(p.failed_attempt_count, 0)                          as failed_attempt_count,
    coalesce(p.amount_paid, 0)::numeric(12, 2)                   as amount_paid,
    coalesce(p.amount_refunded, 0)::numeric(12, 2)               as amount_refunded,
    p.first_paid_at,
    (o.total_amount - coalesce(p.amount_paid, 0))::numeric(12, 2) as balance_due,
    o.order_status not in ('cancelled') and coalesce(p.amount_paid, 0) > 0 as is_revenue_order,
    case when o.order_status not in ('cancelled') and coalesce(p.amount_paid, 0) > 0
         then (o.total_amount - o.tax_amount - coalesce(p.amount_refunded, 0))
         else 0 end::numeric(12, 2)                              as net_revenue
from {{ ref('stg_shopfront__orders') }} o
left join {{ ref('int_orders__item_summary') }} i on i.order_id = o.order_id
left join {{ ref('int_orders__payment_summary') }} p on p.order_id = o.order_id
