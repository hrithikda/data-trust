-- Order fact: one row per order with revenue, cash and customer attributes.
select
    o.order_id,
    o.customer_id,
    o.subscription_id,
    o.campaign_id,
    o.order_status,
    o.sales_channel,
    o.currency,
    o.ordered_at,
    o.order_date,
    o.subtotal_amount,
    o.discount_amount,
    o.shipping_amount,
    o.tax_amount,
    o.total_amount,
    o.line_count,
    o.units,
    o.amount_paid,
    o.amount_refunded,
    o.balance_due,
    o.is_revenue_order,
    o.net_revenue,
    c.country_code          as customer_country_code,
    c.acquisition_channel   as customer_acquisition_channel,
    c.is_subscriber         as customer_is_subscriber
from {{ ref('int_orders__enriched') }} o
left join {{ ref('dim_customers') }} c on c.customer_id = o.customer_id
