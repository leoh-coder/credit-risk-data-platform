{#- Grain: one operation. Operations whose customer was quarantined point to the unknown customer. -#}

with operations as (

    select * from {{ ref('stg_core_banking__credit_operations') }}

),

performance as (

    select * from {{ ref('int_credit_operations__performance') }}

),

customers as (

    select * from {{ ref('dim_customers') }} where not is_unknown_member

),

products as (

    select * from {{ ref('dim_products') }} where not is_unknown_member

)

select
    o.operation_id::text                                                    as operation_id,
    coalesce(c.customer_key, {{ unknown_key() }})                           as customer_key,
    coalesce(p.product_key, {{ unknown_key() }})                            as product_key,
    {{ date_key('o.contract_date') }}                                       as contract_date_key,

    o.principal_amount,
    o.interest_rate_monthly,
    o.installments_count,
    o.installment_amount,
    (o.installments_count * o.installment_amount)::numeric(18, 2)           as total_scheduled_amount,
    o.bureau_score_at_origination,
    (o.installment_amount / nullif(c.monthly_income, 0))::numeric(10, 4)   as installment_to_income_ratio,

    perf.window_installments,
    perf.window_installments_present::int                                   as window_installments_present,
    perf.is_window_complete,
    perf.max_dpd_in_window,
    perf.has_defaulted_to_date,
    perf.window_last_due_date,
    perf.is_mature,
    perf.pd_label,
    perf.label_status,

    o.batch_id::text                                                        as batch_id,
    o.observed_at

from operations as o
inner join performance as perf using (operation_id)
left join customers as c on c.customer_id = o.customer_id
left join products as p on p.product_type = o.product_type
