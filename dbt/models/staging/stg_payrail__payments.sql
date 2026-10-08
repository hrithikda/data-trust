select
    payment_id::text                 as payment_id,
    order_id::text                   as order_id,
    customer_id::text                as customer_id,
    payment_method::text             as payment_method,
    lower(payment_status)::text      as payment_status,
    amount::numeric(12, 2)           as amount,
    upper(currency)::text            as currency,
    processed_at::timestamp          as processed_at,
    _loaded_at::timestamp            as loaded_at
from {{ source('payrail', 'payments') }}
