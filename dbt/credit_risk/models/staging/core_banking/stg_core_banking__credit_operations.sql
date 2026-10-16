with source as (

    select * from {{ source('core_banking', 'credit_operations') }}

),

renamed as (

    select
        operation_id,
        customer_id,
        product_type,
        contract_date::date             as contract_date,
        -- money and rates as exact numerics: the lake ships IEEE doubles
        principal_amount::numeric(18, 2)        as principal_amount,
        interest_rate_monthly::numeric(10, 6)   as interest_rate_monthly,
        installments_count::int                 as installments_count,
        installment_amount::numeric(18, 2)      as installment_amount,
        bureau_score_at_origination::int        as bureau_score_at_origination,
        _batch_id                       as batch_id,
        _extracted_at                   as observed_at,
        _loaded_at                      as loaded_at

    from source

)

select * from renamed
