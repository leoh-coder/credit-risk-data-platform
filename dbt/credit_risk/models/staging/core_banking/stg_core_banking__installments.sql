with source as (

    select * from {{ source('core_banking', 'installments') }}

),

renamed as (

    select
        installment_id,
        operation_id,
        installment_number::int         as installment_number,
        due_date::date                  as due_date,
        amount_due::numeric(18, 2)      as amount_due,
        payment_date::date              as payment_date,
        amount_paid::numeric(18, 2)     as amount_paid,
        -- the observation clock: delinquency is measured against it, never against current_date
        _extracted_at::date             as observation_date,
        _batch_id                       as batch_id,
        _extracted_at                   as observed_at,
        _loaded_at                      as loaded_at

    from source

)

select * from renamed
