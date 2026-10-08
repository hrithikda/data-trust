-- Campaign ROI: attributed orders, revenue, new customers, ROAS and CAC.
with attributed as (
    select
        campaign_id,
        count(*)                                    as attributed_orders,
        sum(net_revenue)::numeric(14, 2)            as attributed_revenue,
        count(*) filter (where is_new_customer_order) as new_customers
    from {{ ref('int_campaigns__attributed_orders') }}
    group by campaign_id
)

select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    c.started_on,
    c.ended_on,
    c.budget_amount,
    coalesce(a.attributed_orders, 0)                                   as attributed_orders,
    coalesce(a.attributed_revenue, 0)::numeric(14, 2)                  as attributed_revenue,
    coalesce(a.new_customers, 0)                                       as new_customers,
    (coalesce(a.attributed_revenue, 0) / nullif(c.budget_amount, 0))::numeric(8, 2) as roas,
    (c.budget_amount / nullif(a.new_customers, 0))::numeric(12, 2)     as customer_acquisition_cost
from {{ ref('stg_campaignhub__campaigns') }} c
left join attributed a on a.campaign_id = c.campaign_id
