-- Customer health (0-100): recency, subscription, support experience and value.
with inputs as (
    select
        c.customer_id,
        c.customer_status,
        c.is_subscriber,
        c.days_since_last_order,
        coalesce(s.open_ticket_count, 0)  as open_ticket_count,
        coalesce(s.tickets_last_90d, 0)   as tickets_last_90d,
        s.avg_csat,
        v.clv_tier,
        v.customer_lifetime_value
    from {{ ref('dim_customers') }} c
    left join {{ ref('int_customers__support_summary') }} s on s.customer_id = c.customer_id
    left join {{ ref('customer_lifetime_value') }} v on v.customer_id = c.customer_id
    where c.order_count > 0
),

scored as (
    select
        *,
        case
            when days_since_last_order <= 30 then 40
            when days_since_last_order <= 90 then 30
            when days_since_last_order <= 180 then 15
            else 0
        end as recency_points,
        case when is_subscriber then 20 else 0 end as subscription_points,
        greatest(0, 25 - 6 * open_ticket_count - 2 * tickets_last_90d
                    - case when avg_csat < 3 then 8 else 0 end) as support_points,
        case clv_tier when 'platinum' then 15 when 'gold' then 11 when 'silver' then 7 else 3 end as value_points
    from inputs
)

select
    customer_id,
    customer_status,
    is_subscriber,
    days_since_last_order,
    open_ticket_count,
    avg_csat,
    clv_tier,
    customer_lifetime_value,
    recency_points,
    subscription_points,
    support_points,
    value_points,
    least(100, recency_points + subscription_points + support_points + value_points) as health_score,
    case
        when recency_points + subscription_points + support_points + value_points >= 70 then 'healthy'
        when recency_points + subscription_points + support_points + value_points >= 40 then 'at_risk'
        else 'critical'
    end as health_band
from scored
