with catalog as (

    select * from {{ ref('product_catalog') }}

)

select
    {{ dbt_utils.generate_surrogate_key(['product_type']) }}  as product_key,
    product_type,
    product_name,
    is_payroll_deductible,
    is_collateralized,
    false                                                   as is_unknown_member
from catalog

union all

select
    {{ unknown_key() }},
    'unknown',
    'Unknown product',
    null,
    null,
    true
