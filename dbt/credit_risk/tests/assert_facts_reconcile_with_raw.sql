-- Every raw row must reach its fact table: orphans go to the unknown member, they are never dropped.
-- Returns the mismatching facts (a passing test returns no rows).

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
