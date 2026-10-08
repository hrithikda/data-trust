-- Monthly recurring revenue contribution per subscription, as reported by Rebill status.
select
    s.subscription_id,
    s.customer_id,
    s.plan_code,
    s.subscription_status,
    s.billing_interval,
    s.monthly_amount,
    s.started_on,
    s.ended_on,
    s.subscription_status = 'active'                                       as is_active,
    case when s.subscription_status = 'active' then s.monthly_amount else 0 end::numeric(10, 2) as mrr,
    (extract(year from age(coalesce(least(s.ended_on, {{ reference_date() }}), {{ reference_date() }}), s.started_on)) * 12
     + extract(month from age(coalesce(least(s.ended_on, {{ reference_date() }}), {{ reference_date() }}), s.started_on)))::integer
                                                                           as tenure_months
from {{ ref('stg_rebill__subscriptions') }} s
