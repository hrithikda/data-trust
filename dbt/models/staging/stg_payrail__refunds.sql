select
    refund_id::text                  as refund_id,
    payment_id::text                 as payment_id,
    refund_amount::numeric(12, 2)    as refund_amount,
    refund_reason::text              as refund_reason,
    refunded_at::timestamp           as refunded_at,
    _loaded_at::timestamp            as loaded_at
from {{ source('payrail', 'refunds') }}
