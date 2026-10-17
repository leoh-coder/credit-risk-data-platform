-- Complete schedules must match the contract: installments 1..n, strictly increasing due dates,
-- each installment equal to the contracted amount.

with operations as (

    select operation_id, installments_count, installment_amount
    from {{ ref('stg_core_banking__credit_operations') }}

),

installments as (

    select
        operation_id,
        installment_number,
        amount_due,
        due_date,
        lag(due_date) over (partition by operation_id order by installment_number) as previous_due_date
    from {{ ref('stg_core_banking__installments') }}

),

per_operation as (

    select
        o.operation_id,
        o.installments_count,
        count(*)                                                            as installments_present,
        min(i.installment_number)                                           as first_number,
        max(i.installment_number)                                           as last_number,
        sum(case when i.amount_due <> o.installment_amount then 1 else 0 end)     as amount_mismatches,
        sum(case when i.due_date <= i.previous_due_date then 1 else 0 end)         as non_increasing_due_dates
    from operations as o
    inner join installments as i using (operation_id)
    group by o.operation_id, o.installments_count

)

select *
from per_operation
where installments_present = installments_count
  and (
      first_number <> 1
      or last_number <> installments_count
      or amount_mismatches > 0
      or non_increasing_due_dates > 0
  )
