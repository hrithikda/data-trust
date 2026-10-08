-- Month-end close: recognised revenue alongside cash collected, by currency.
with revenue as (
    select
        date_trunc('month', order_date)::date as revenue_month,
        currency,
        sum(orders)        as orders,
        sum(gross_sales)   as gross_sales,
        sum(discounts)     as discounts,
        sum(refunds)       as refunds,
        sum(net_revenue)   as net_revenue
    from {{ ref('fct_daily_revenue') }}
    group by 1, 2
),

cash as (
    select
        date_trunc('month', processed_at)::date as revenue_month,
        currency,
        sum(amount) filter (where payment_status = 'succeeded') as cash_collected,
        sum(refunded_amount)                                     as cash_refunded
    from {{ ref('fct_payments') }}
    group by 1, 2
)

select
    coalesce(r.revenue_month, c.revenue_month)         as revenue_month,
    coalesce(r.currency, c.currency)                   as currency,
    coalesce(r.orders, 0)                              as orders,
    coalesce(r.gross_sales, 0)::numeric(14, 2)         as gross_sales,
    coalesce(r.discounts, 0)::numeric(14, 2)           as discounts,
    coalesce(r.refunds, 0)::numeric(14, 2)             as refunds,
    coalesce(r.net_revenue, 0)::numeric(14, 2)         as net_revenue,
    coalesce(c.cash_collected, 0)::numeric(14, 2)      as cash_collected,
    coalesce(c.cash_refunded, 0)::numeric(14, 2)       as cash_refunded
from revenue r
full outer join cash c on c.revenue_month = r.revenue_month and c.currency = r.currency
