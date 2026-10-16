{#-
    Behavioral features from the Open Finance history shared at onboarding.
    Only transactions strictly BEFORE the application (registration_date) and inside the
    history window are used: anything else would leak post-decision behavior into the model.
-#}

{%- set history_days = var('open_finance_history_days') -%}

with customers as (

    select customer_id, registration_date, monthly_income
    from {{ ref('stg_core_banking__customers') }}

),

transactions as (

    select t.*
    from {{ ref('stg_open_finance__transactions') }} as t
    inner join customers as c using (customer_id)
    where t.transacted_at <  c.registration_date
      and t.transacted_at >= c.registration_date - interval '{{ history_days }} days'

),

aggregated as (

    select
        customer_id,
        count(*)                                                                    as tx_count,
        sum(amount) filter (where direction = 'credit')                             as inflow_amount,
        sum(amount) filter (where direction = 'debit')                              as outflow_amount,
        count(*) filter (where transaction_type = 'salary_deposit')                 as salary_deposit_count,
        sum(amount) filter (where merchant_category = 'betting')                    as betting_amount,
        sum(amount) filter (where merchant_category is not null)                    as merchant_spend_amount,
        sum(amount) filter (where transaction_type = 'cash_withdrawal')             as cash_withdrawal_amount
    from transactions
    group by customer_id

)

select
    c.customer_id,
    coalesce(a.tx_count, 0)                                                         as of_tx_count,
    coalesce(a.salary_deposit_count, 0)                                             as of_salary_deposit_count,
    round(coalesce(a.inflow_amount, 0) / ({{ history_days }} / 30.0), 2)           as of_monthly_inflow,
    round(coalesce(a.outflow_amount, 0) / ({{ history_days }} / 30.0), 2)          as of_monthly_outflow,
    round((coalesce(a.inflow_amount, 0) / ({{ history_days }} / 30.0)) / c.monthly_income, 4)
                                                                                    as of_inflow_to_income_ratio,
    round(coalesce(a.betting_amount, 0) / nullif(a.merchant_spend_amount, 0), 4)    as of_betting_spend_share,
    round(coalesce(a.cash_withdrawal_amount, 0) / nullif(a.outflow_amount, 0), 4)   as of_cash_withdrawal_share

from customers as c
left join aggregated as a using (customer_id)
