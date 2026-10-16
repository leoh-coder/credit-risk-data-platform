{#-
    PII boundary: name, e-mail, phone and CPF never leave this model in clear text.
    Materialized as a TABLE on purpose: a view would store the salt in its definition, and
    Postgres exposes view definitions to any role through pg_views.
-#}
{{ config(materialized='table') }}

with source as (

    select * from {{ source('core_banking', 'customers') }}

),

renamed as (

    select
        customer_id,
        encode(
            sha256(convert_to('{{ env_var("DBT_PII_SALT") }}' || regexp_replace(cpf, '\D', '', 'g'), 'UTF8')),
            'hex'
        )                                                   as cpf_hash,
        lower(split_part(email, '@', 2))                    as email_domain,
        -- national format: last 11 digits (with or without +55); the area code is the first 2 of them
        case
            when length(regexp_replace(phone_number, '\D', '', 'g')) in (11, 13)
                then left(right(regexp_replace(phone_number, '\D', '', 'g'), 11), 2)
        end                                                 as phone_area_code,
        birth_date::date                                    as birth_date,
        state                                               as state_code,
        employment_type,
        monthly_income::numeric(18, 2)                      as monthly_income,
        registration_date::date                             as registration_date,
        _batch_id                                           as batch_id,
        _extracted_at                                       as observed_at,
        _loaded_at                                          as loaded_at

    from source

)

select * from renamed
