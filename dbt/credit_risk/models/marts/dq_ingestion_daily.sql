{#-
    Ingestion data quality per reference_date x entity, from the LATEST load of each date.
    Quarantined rows stay in the lake; only their counts travel through
    audit.lake_loads. A date whose latest load predates those counts is left out rather than
    reported with stale numbers from an older load.

    The quarantine budget is evaluated on a rolling window of the last 7 loaded dates per entity
    because daily denominators of ~30 rows made the daily rate pure noise. The window
    rate is sum(quarantined) / sum(received), not an average of daily rates, so small days weigh less.
    ROWS counts loaded dates, not calendar days (a rejected date simply is not in the window).
-#}

with ranked_loads as (

    select
        reference_date,
        batch_id,
        contract_version,
        manifest_entities,
        finished_at,
        row_number() over (partition by reference_date order by finished_at desc)  as load_rank
    from {{ source('loader', 'lake_loads') }}

),

per_entity as (

    select
        l.reference_date,
        l.batch_id,
        l.contract_version,
        l.finished_at                                   as loaded_at,
        e.key                                           as entity,
        (e.value ->> 'rows_received')::int              as rows_received,
        (e.value ->> 'rows_valid')::int                 as rows_valid,
        (e.value ->> 'rows_quarantined')::int           as rows_quarantined
    from ranked_loads as l
    cross join lateral jsonb_each(l.manifest_entities) as e
    where l.load_rank = 1

),

rolling as (

    select
        *,
        sum(rows_received) over (
            partition by entity order by reference_date rows between 6 preceding and current row
        )                                               as rows_received_7d,
        sum(rows_quarantined) over (
            partition by entity order by reference_date rows between 6 preceding and current row
        )                                               as rows_quarantined_7d,
        count(*) over (
            partition by entity order by reference_date rows between 6 preceding and current row
        )                                               as dates_in_window_7d
    from per_entity

)

select
    {{ dbt_utils.generate_surrogate_key(['reference_date', 'entity']) }}           as ingestion_quality_key,
    {{ date_key('reference_date') }}                                                as reference_date_key,
    reference_date,
    entity,
    rows_received,
    rows_valid,
    rows_quarantined,
    (100.0 * rows_quarantined / nullif(rows_received, 0))::numeric(6, 2)           as quarantine_pct,
    rows_received_7d::int                                                           as rows_received_7d,
    rows_quarantined_7d::int                                                        as rows_quarantined_7d,
    dates_in_window_7d::int                                                         as dates_in_window_7d,
    (100.0 * rows_quarantined_7d / nullif(rows_received_7d, 0))::numeric(6, 2)     as quarantine_pct_7d,
    batch_id,
    contract_version,
    loaded_at
from rolling
