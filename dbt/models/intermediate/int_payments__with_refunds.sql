-- Each payment attempt with the refunds issued against it.
with refunds as (
    select
        payment_id,
        count(*)            as refund_count,
        sum(refund_amount)  as refunded_amount,
        max(refunded_at)    as last_refunded_at
    from {{ ref('stg_payrail__refunds') }}
    group by payment_id
)

select
    p.payment_id,
    p.order_id,
    p.customer_id,
    p.payment_method,
    p.payment_status,
    p.amount,
    p.currency,
    p.processed_at,
    coalesce(r.refund_count, 0)                         as refund_count,
    coalesce(r.refunded_amount, 0)::numeric(12, 2)      as refunded_amount,
    r.last_refunded_at,
    (case when p.payment_status = 'succeeded' then p.amount else 0 end
        - coalesce(r.refunded_amount, 0))::numeric(12, 2) as net_amount
from {{ ref('stg_payrail__payments') }} p
left join refunds r on r.payment_id = p.payment_id
