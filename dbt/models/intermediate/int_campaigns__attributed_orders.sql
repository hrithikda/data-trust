-- Revenue orders credited to a campaign (promo-code attribution), flagged when they acquired the customer.
select
    o.order_id,
    o.campaign_id,
    o.customer_id,
    o.ordered_at,
    o.net_revenue,
    o.ordered_at = h.first_order_at as is_new_customer_order
from {{ ref('int_orders__enriched') }} o
left join {{ ref('int_customers__order_history') }} h on h.customer_id = o.customer_id
where o.campaign_id is not null
  and o.is_revenue_order
