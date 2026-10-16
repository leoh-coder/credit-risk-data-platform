{#-
    Days past due (DPD) of each installment, measured against the observation clock.
      paid            -> how late the observed payment was (early payments count as 0)
      unpaid, due     -> observation_date - due_date (still running)
      unpaid, not due -> 0 (nothing owed yet; NOT evidence of good behavior)
-#}

with installments as (

    select * from {{ ref('stg_core_banking__installments') }}

),

delinquency as (

    select
        installment_id,
        operation_id,
        installment_number,
        due_date,
        amount_due,
        payment_date,
        amount_paid,
        observation_date,
        payment_date is not null                    as is_paid,
        due_date <= observation_date                as is_due,
        case
            when payment_date is not null then greatest(payment_date - due_date, 0)
            when due_date <= observation_date then observation_date - due_date
            else 0
        end                                         as days_past_due

    from installments

)

select
    *,
    case
        when days_past_due = 0 then 'current'
        when days_past_due <= 30 then '1-30'
        when days_past_due <= 60 then '31-60'
        when days_past_due < {{ var('pd_default_dpd') }} then '61-89'
        else '90+'  -- same threshold as the default definition
    end as dpd_bucket

from delinquency
