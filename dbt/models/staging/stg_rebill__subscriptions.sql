select
    subscription_id::text            as subscription_id,
    customer_id::text                as customer_id,
    plan_code::text                  as plan_code,
    lower(subscription_status)::text as subscription_status,
    billing_interval::text           as billing_interval,
    price_per_cycle::numeric(10, 2)  as price_per_cycle,
    -- normalise to a monthly amount: biweekly plans bill 26 times a year
    round(case when billing_interval = 'biweekly' then price_per_cycle * 26 / 12
               else price_per_cycle end, 2)::numeric(10, 2) as monthly_amount,
    started_on::date                 as started_on,
    ended_on::date                   as ended_on,
    _loaded_at::timestamp            as loaded_at
from {{ source('rebill', 'subscriptions') }}
