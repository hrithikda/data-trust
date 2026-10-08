select
    product_id::text                 as product_id,
    sku::text                        as sku,
    product_name::text               as product_name,
    category::text                   as category,
    unit_price::numeric(10, 2)       as list_price,
    is_active::boolean               as is_active,
    launched_on::date                as launched_on,
    _loaded_at::timestamp            as loaded_at
from {{ source('shopfront', 'products') }}
