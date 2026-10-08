-- Daily revenue by currency from revenue orders (order-date basis).
select
    order_date,
    currency,
    count(*)                                   as orders,
    sum(units)                                 as units,
    sum(subtotal_amount)::numeric(14, 2)       as gross_sales,
    sum(discount_amount)::numeric(14, 2)       as discounts,
    sum(amount_refunded)::numeric(14, 2)       as refunds,
    sum(net_revenue)::numeric(14, 2)           as net_revenue,
    (sum(net_revenue) / count(*))::numeric(12, 2) as average_order_value
from {{ ref('fct_orders') }}
where is_revenue_order
group by order_date, currency
