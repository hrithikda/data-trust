select
    order_id::text                   as order_id,
    customer_id::text                as customer_id,
    subscription_id::text            as subscription_id,
    campaign_id::text                as campaign_id,
    lower(order_status)::text        as order_status,
    sales_channel::text              as sales_channel,
    upper(currency)::text            as currency,
    subtotal_amount::numeric(12, 2)  as subtotal_amount,
    discount_amount::numeric(12, 2)  as discount_amount,
    shipping_amount::numeric(12, 2)  as shipping_amount,
    tax_amount::numeric(12, 2)       as tax_amount,
    total_amount::numeric(12, 2)     as total_amount,
    ordered_at::timestamp            as ordered_at,
    _loaded_at::timestamp            as loaded_at
from {{ source('shopfront', 'orders') }}
