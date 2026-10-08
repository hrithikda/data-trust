-- Monthly executive scorecard: revenue, customers, recurring revenue, marketing efficiency and health.
with revenue as (
    select revenue_month, sum(orders) as orders, sum(net_revenue) as net_revenue,
           sum(refunds) as refunds, sum(cash_collected) as cash_collected
    from {{ ref('fin_monthly_revenue_close') }}
    group by revenue_month
),

customers as (
    select
        date_trunc('month', order_date)::date as revenue_month,
        count(distinct customer_id)           as active_customers
    from {{ ref('fct_orders') }}
    where is_revenue_order
    group by 1
),

recurring as (
    select
        r.revenue_month,
        count(s.subscription_id)          as active_subscriptions,
        coalesce(sum(s.monthly_amount), 0) as ending_mrr
    from revenue r
    left join {{ ref('fct_subscriptions') }} s
        on s.started_on <= (r.revenue_month + interval '1 month' - interval '1 day')::date
       and (s.ended_on is null
            or s.ended_on > (r.revenue_month + interval '1 month' - interval '1 day')::date
            or s.is_active)  -- Rebill status is the system of record for active subscriptions
       and s.subscription_status <> 'paused'
    group by r.revenue_month
),

marketing as (
    select date_trunc('month', started_on)::date as revenue_month,
           sum(budget_amount) as marketing_spend, sum(new_customers) as new_customers
    from {{ ref('campaign_performance') }}
    group by 1
),

health as (
    select date_trunc('month', {{ reference_date() }})::date as revenue_month,
           avg(health_score)::numeric(5, 1) as avg_health_score,
           avg(case when health_band <> 'healthy' then 1.0 else 0 end)::numeric(5, 3) as share_customers_at_risk
    from {{ ref('customer_health_scores') }}
)

select
    r.revenue_month,
    r.orders,
    r.net_revenue::numeric(14, 2)                                        as net_revenue,
    r.refunds::numeric(14, 2)                                            as refunds,
    r.cash_collected::numeric(14, 2)                                     as cash_collected,
    (r.net_revenue / nullif(r.orders, 0))::numeric(12, 2)                as average_order_value,
    coalesce(c.active_customers, 0)                                      as active_customers,
    rc.active_subscriptions,
    rc.ending_mrr::numeric(14, 2)                                        as ending_mrr,
    coalesce(m.marketing_spend, 0)::numeric(14, 2)                       as marketing_spend,
    coalesce(m.new_customers, 0)                                         as campaign_new_customers,
    (m.marketing_spend / nullif(m.new_customers, 0))::numeric(12, 2)     as blended_cac,
    h.avg_health_score,
    h.share_customers_at_risk
from revenue r
left join customers c on c.revenue_month = r.revenue_month
left join recurring rc on rc.revenue_month = r.revenue_month
left join marketing m on m.revenue_month = r.revenue_month
left join health h on h.revenue_month = r.revenue_month
