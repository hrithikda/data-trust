-- Subscription fact with MRR contribution and customer geography.
select
    s.subscription_id,
    s.customer_id,
    s.plan_code,
    s.subscription_status,
    s.billing_interval,
    s.monthly_amount,
    s.started_on,
    s.ended_on,
    s.is_active,
    s.mrr,
    s.tenure_months,
    c.country_code as customer_country_code
from {{ ref('int_subscriptions__mrr') }} s
left join {{ ref('dim_customers') }} c on c.customer_id = s.customer_id
