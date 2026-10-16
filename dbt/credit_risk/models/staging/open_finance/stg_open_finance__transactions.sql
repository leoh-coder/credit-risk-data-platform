with source as (

    select * from {{ source('open_finance', 'transactions') }}

),

renamed as (

    select
        transaction_id,
        customer_id,
        transaction_ts                  as transacted_at,
        transaction_type,
        direction,
        amount::numeric(18, 2)          as amount,
        merchant_category,
        channel,
        _batch_id                       as batch_id,
        _extracted_at                   as observed_at,
        _loaded_at                      as loaded_at

    from source

)

select * from renamed
