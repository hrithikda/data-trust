-- Product dimension with lifetime sales from revenue orders.
with sales as (
    select
        i.product_id,
        sum(i.quantity)                     as units_sold,
        sum(i.line_amount)::numeric(14, 2)  as gross_sales,
        count(distinct i.order_id)          as order_count
    from {{ ref('stg_shopfront__order_items') }} i
    join {{ ref('int_orders__enriched') }} o on o.order_id = i.order_id
    where o.is_revenue_order
    group by i.product_id
)

select
    p.product_id,
    p.sku,
    p.product_name,
    p.category,
    p.list_price,
    p.is_active,
    p.launched_on,
    coalesce(s.units_sold, 0)                    as units_sold,
    coalesce(s.gross_sales, 0)::numeric(14, 2)   as gross_sales,
    coalesce(s.order_count, 0)                   as order_count
from {{ ref('stg_shopfront__products') }} p
left join sales s on s.product_id = p.product_id
