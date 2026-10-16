{#-
    Probability-of-default label per credit operation.

    Default = DPD >= pd_default_dpd on any of the first pd_window_installments installments
    (fewer if the contract is shorter). Censoring rules:
      - a GOOD outcome is only known once the whole window had time to reach the threshold (is_mature);
      - a BAD outcome is only labeled on mature contracts too: labeling early bads while immature goods
        stay unlabeled would over-sample defaults in recent cohorts;
      - a gap in the window (installment quarantined in the lake) cannot hide a default that was already
        observed, but it can hide one, so an unseen default is never assumed to be a good.
-#}

{%- set window_size = var('pd_window_installments') -%}
{%- set threshold = var('pd_default_dpd') -%}

with operations as (

    select * from {{ ref('stg_core_banking__credit_operations') }}

),

installments as (

    select * from {{ ref('int_installments__delinquency') }}

),

windows as (

    select
        operation_id,
        contract_date,
        observed_at::date                                           as observation_date,
        least(installments_count, {{ window_size }})                as window_installments
    from operations

),

window_performance as (

    select
        w.operation_id,
        count(i.installment_id)                                     as window_installments_present,
        coalesce(max(i.days_past_due), 0)                           as max_dpd_in_window
    from windows as w
    left join installments as i
        on  i.operation_id = w.operation_id
        and i.installment_number <= w.window_installments
    group by w.operation_id

),

flags as (

    select
        w.operation_id,
        w.window_installments,
        p.window_installments_present,
        p.window_installments_present = w.window_installments      as is_window_complete,
        p.max_dpd_in_window,
        p.max_dpd_in_window >= {{ threshold }}                      as has_defaulted_to_date,
        -- installment k is due k months after the contract; Postgres month arithmetic never lands
        -- earlier than the source schedule, so maturity is never declared too early
        (w.contract_date + make_interval(months => w.window_installments))::date
                                                                    as window_last_due_date,
        (w.contract_date + make_interval(months => w.window_installments))::date
            + {{ threshold }} <= w.observation_date                 as is_mature
    from windows as w
    inner join window_performance as p using (operation_id)

)

select
    *,
    case
        when not is_mature then null
        when has_defaulted_to_date then 1
        when not is_window_complete then null
        else 0
    end as pd_label,
    case
        when not is_mature then 'immature'
        when has_defaulted_to_date or is_window_complete then 'labeled'
        else 'incomplete_schedule'
    end as label_status

from flags
