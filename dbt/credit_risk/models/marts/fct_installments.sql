{#- Grain: one installment. Installments whose operation was quarantined point to the unknown members. -#}

with installments as (

    select * from {{ ref('int_installments__delinquency') }}

),

operations as (

    select operation_id, customer_id, product_type from {{ ref('stg_core_banking__credit_operations') }}

),

customers as (

    select * from {{ ref('dim_customers') }} where not is_unknown_member

),

products as (

    select * from {{ ref('dim_products') }} where not is_unknown_member

)

select
    i.installment_id::text                              as installment_id,
    i.operation_id::text                                as operation_id,
    coalesce(c.customer_key, {{ unknown_key() }})       as customer_key,
    coalesce(p.product_key, {{ unknown_key() }})        as product_key,
    {{ date_key('i.due_date') }}                        as due_date_key,
    {{ date_key('i.payment_date') }}                    as payment_date_key,

    i.installment_number,
    i.amount_due,
    i.amount_paid,
    (case when i.is_due and not i.is_paid then i.amount_due else 0 end)::numeric(18, 2)
                                                        as amount_overdue,
    i.is_paid,
    i.is_due,
    i.days_past_due,
    i.dpd_bucket,
    i.observation_date

from installments as i
left join operations as o using (operation_id)
left join customers as c on c.customer_id = o.customer_id
left join products as p on p.product_type = o.product_type
