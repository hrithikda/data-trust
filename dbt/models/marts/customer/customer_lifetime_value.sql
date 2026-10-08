-- Customer lifetime value: realised net revenue plus a simple 12-month forward estimate.
with base as (
    select
        customer_id,
        country_code,
        acquisition_channel,
        customer_status,
        is_subscriber,
        order_count,
        lifetime_net_revenue,
        mrr,
        greatest(1, (({{ reference_date() }} - first_order_at::date) / 30.4)::numeric) as tenure_months
    from {{ ref('dim_customers') }}
    where order_count > 0
),

valued as (
    select
        *,
        (lifetime_net_revenue / tenure_months)::numeric(12, 2) as monthly_value,
        (case
            when is_subscriber then 0.85
            when customer_status = 'active' then 0.55
            when customer_status = 'inactive' then 0.25
            else 0.05
        end)::numeric(4, 2) as retention_factor
    from base
)

select
    customer_id,
    country_code,
    acquisition_channel,
    customer_status,
    is_subscriber,
    order_count,
    round(tenure_months, 1)                                          as tenure_months,
    lifetime_net_revenue                                             as historical_value,
    monthly_value,
    (greatest(monthly_value, mrr) * 12 * retention_factor)::numeric(12, 2) as predicted_12m_value,
    (lifetime_net_revenue + greatest(monthly_value, mrr) * 12 * retention_factor)::numeric(14, 2) as customer_lifetime_value,
    case
        when lifetime_net_revenue + greatest(monthly_value, mrr) * 12 * retention_factor >= 1000 then 'platinum'
        when lifetime_net_revenue + greatest(monthly_value, mrr) * 12 * retention_factor >= 400 then 'gold'
        when lifetime_net_revenue + greatest(monthly_value, mrr) * 12 * retention_factor >= 150 then 'silver'
        else 'bronze'
    end                                                              as clv_tier
from valued
