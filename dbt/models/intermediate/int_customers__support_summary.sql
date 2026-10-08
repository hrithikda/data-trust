-- Support experience per customer.
select
    customer_id,
    count(*)                                                                as ticket_count,
    count(*) filter (where ticket_status in ('open', 'pending'))            as open_ticket_count,
    count(*) filter (where priority in ('high', 'urgent'))                  as escalated_ticket_count,
    count(*) filter (where opened_at >= {{ reference_date() }} - interval '90 days') as tickets_last_90d,
    avg(csat_score)::numeric(4, 2)                                          as avg_csat,
    max(opened_at)                                                          as last_ticket_at
from {{ ref('stg_deskline__tickets') }}
group by customer_id
