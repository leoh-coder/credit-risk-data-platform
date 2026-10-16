{#-
    One row per load of a lake date into raw (audit.lake_loads), so the engineering view of the
    dashboard reads marts only. Freshness = max(finished_at).
-#}

with loads as (

    select * from {{ source('loader', 'lake_loads') }}

),

totals as (

    select
        l.load_id,
        sum(cast(e.value as bigint))                    as rows_loaded
    from loads as l
    cross join lateral jsonb_each_text(l.rows_loaded) as e
    group by l.load_id

)

select
    l.load_id::text                                                                     as load_id,
    {{ date_key('l.reference_date') }}                                                  as reference_date_key,
    l.reference_date,
    l.batch_id::text                                                                    as batch_id,
    l.contract_version::text                                                            as contract_version,
    t.rows_loaded::bigint                                                               as rows_loaded,
    l.started_at,
    l.finished_at,
    cast({{ dbt.datediff('l.started_at', 'l.finished_at', 'millisecond') }} / 1000.0 as numeric(10, 3))
                                                                                        as load_seconds,
    row_number() over (partition by l.reference_date order by l.finished_at desc) = 1  as is_latest_for_date
from loads as l
inner join totals as t using (load_id)
