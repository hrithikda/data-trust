-- Order totals must equal subtotal - discount + shipping + tax (one-cent tolerance).
select order_id, subtotal_amount, discount_amount, shipping_amount, tax_amount, total_amount
from {{ ref('stg_shopfront__orders') }}
where abs(total_amount - (subtotal_amount - discount_amount + shipping_amount + tax_amount)) > 0.01
