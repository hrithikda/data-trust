-- Line-level rollup per order, including product mix.
select
    i.order_id,
    count(*)                                         as line_count,
    sum(i.quantity)                                  as units,
    sum(i.line_amount)::numeric(12, 2)               as items_subtotal,
    count(distinct p.category)                       as category_count,
    bool_or(p.category = 'equipment')                as has_equipment,
    string_agg(distinct p.category, ', ')            as categories
from {{ ref('stg_shopfront__order_items') }} i
left join {{ ref('stg_shopfront__products') }} p on p.product_id = i.product_id
group by i.order_id
