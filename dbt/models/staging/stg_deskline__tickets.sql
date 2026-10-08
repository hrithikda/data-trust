select
    ticket_id::text                  as ticket_id,
    customer_id::text                as customer_id,
    order_id::text                   as order_id,
    channel::text                    as channel,
    priority::text                   as priority,
    ticket_status::text              as ticket_status,
    opened_at::timestamp             as opened_at,
    solved_at::timestamp             as solved_at,
    csat_score::integer              as csat_score,
    _loaded_at::timestamp            as loaded_at
from {{ source('deskline', 'tickets') }}
