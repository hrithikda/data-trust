select
    order_item_id::text              as order_item_id,
    order_id::text                   as order_id,
    product_id::text                 as product_id,
    quantity::integer                as quantity,
    unit_price::numeric(10, 2)       as unit_price,
    line_amount::numeric(12, 2)      as line_amount,
    _loaded_at::timestamp            as loaded_at
from {{ source('shopfront', 'order_items') }}
