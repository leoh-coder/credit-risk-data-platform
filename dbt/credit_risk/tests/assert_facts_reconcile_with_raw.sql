-- Every raw row reaches its fact table; orphans go to the unknown member instead of being dropped.

with counts as (

    select
        'fct_credit_operations'                                     as fact,
        (select count(*) from {{ ref('fct_credit_operations') }})   as fact_rows,
        (select count(*) from {{ source('core_banking', 'credit_operations') }}) as raw_rows

    union all

    select
        'fct_installments',
        (select count(*) from {{ ref('fct_installments') }}),
        (select count(*) from {{ source('core_banking', 'installments') }})

)

select * from counts where fact_rows <> raw_rows
