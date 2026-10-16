{#- One row per known customer plus the unknown member. No PII: cpf_hash stays in staging. -#}

with customers as (

    select * from {{ ref('stg_core_banking__customers') }}

),

features as (

    select * from {{ ref('int_customers__open_finance_features') }}

),

known as (

    select
        {{ dbt_utils.generate_surrogate_key(['c.customer_id']) }}         as customer_key,
        c.customer_id::text                                             as customer_id,
        c.registration_date,
        c.state_code::text                                              as state_code,
        c.phone_area_code,
        c.email_domain,
        c.employment_type::text                                         as employment_type,
        c.monthly_income,
        case
            when c.monthly_income < 2000 then '< 2k'
            when c.monthly_income < 5000 then '2k-5k'
            when c.monthly_income < 10000 then '5k-10k'
            else '10k+'
        end                                                             as income_band,
        date_part('year', age(c.registration_date, c.birth_date))::int  as age_at_registration,
        f.of_tx_count,
        f.of_salary_deposit_count,
        f.of_monthly_inflow::numeric(18, 2)                             as of_monthly_inflow,
        f.of_monthly_outflow::numeric(18, 2)                            as of_monthly_outflow,
        f.of_inflow_to_income_ratio::numeric(10, 4)                     as of_inflow_to_income_ratio,
        f.of_betting_spend_share::numeric(10, 4)                        as of_betting_spend_share,
        f.of_cash_withdrawal_share::numeric(10, 4)                      as of_cash_withdrawal_share,
        false                                                           as is_unknown_member

    from customers as c
    left join features as f using (customer_id)

),

unknown_member as (

    select
        {{ unknown_key() }}     as customer_key,
        'UNKNOWN'               as customer_id,
        null::date              as registration_date,
        null::text              as state_code,
        null::text              as phone_area_code,
        null::text              as email_domain,
        null::text              as employment_type,
        null::numeric(18, 2)    as monthly_income,
        'unknown'               as income_band,
        null::int               as age_at_registration,
        null::bigint            as of_tx_count,
        null::bigint            as of_salary_deposit_count,
        null::numeric(18, 2)    as of_monthly_inflow,
        null::numeric(18, 2)    as of_monthly_outflow,
        null::numeric(10, 4)    as of_inflow_to_income_ratio,
        null::numeric(10, 4)    as of_betting_spend_share,
        null::numeric(10, 4)    as of_cash_withdrawal_share,
        true                    as is_unknown_member

)

select
    *,
    case
        when age_at_registration is null then 'unknown'
        when age_at_registration < 25 then '18-24'
        when age_at_registration < 35 then '25-34'
        when age_at_registration < 45 then '35-44'
        when age_at_registration < 55 then '45-54'
        when age_at_registration < 65 then '55-64'
        else '65+'
    end as age_band

from (
    select * from known
    union all
    select * from unknown_member
) as all_members
