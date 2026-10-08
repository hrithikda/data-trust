-- Cash position per order: captures, failed attempts and refunds.
select
    order_id,
    count(*) filter (where payment_status = 'succeeded')                       as successful_payment_count,
    count(*) filter (where payment_status = 'failed')                          as failed_attempt_count,
    coalesce(sum(amount) filter (where payment_status = 'succeeded'), 0)::numeric(12, 2) as amount_paid,
    coalesce(sum(refunded_amount), 0)::numeric(12, 2)                          as amount_refunded,
    min(processed_at) filter (where payment_status = 'succeeded')              as first_paid_at,
    max(last_refunded_at)                                                      as last_refunded_at
from {{ ref('int_payments__with_refunds') }}
group by order_id
