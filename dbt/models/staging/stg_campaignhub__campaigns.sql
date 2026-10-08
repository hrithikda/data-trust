select
    campaign_id::text                as campaign_id,
    campaign_name::text              as campaign_name,
    channel::text                    as channel,
    started_on::date                 as started_on,
    ended_on::date                   as ended_on,
    budget_amount::numeric(12, 2)    as budget_amount,
    _loaded_at::timestamp            as loaded_at
from {{ source('campaignhub', 'campaigns') }}
