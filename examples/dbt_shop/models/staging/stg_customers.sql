select
    customer_id,
    full_name,
    country as customer_country
from {{ source('raw', 'customers') }}
