-- Light cleaning only: staging is 1:1 with the source so defects stay observable.
select
    customer_id::text                as customer_id,
    lower(trim(email))::text         as email,
    first_name::text                 as first_name,
    last_name::text                  as last_name,
    upper(country_code)::text        as country_code,
    acquisition_channel::text        as acquisition_channel,
    customer_status::text            as customer_status,
    signup_at::timestamp             as signed_up_at,
    _loaded_at::timestamp            as loaded_at
from {{ source('shopfront', 'customers') }}
