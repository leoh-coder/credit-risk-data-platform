-- Closes the reconciliation chain lake -> raw -> facts: for every date, raw holds exactly the rows
-- the loader recorded in its latest audit.lake_loads entry (no extra, missing or stale dates).

{%- set raw_tables = [
    ('core_banking', 'customers'),
    ('core_banking', 'credit_operations'),
    ('core_banking', 'installments'),
    ('open_finance', 'transactions'),
] %}

with ranked_loads as (

    select
        reference_date,
        rows_loaded,
        row_number() over (partition by reference_date order by finished_at desc) as load_rank
    from {{ source('loader', 'lake_loads') }}

),

latest_load as (

    select reference_date, rows_loaded from ranked_loads where load_rank = 1

),

expected as (

    select l.reference_date, e.key as entity, e.value::bigint as rows_loaded
    from latest_load as l
    cross join lateral jsonb_each_text(l.rows_loaded) as e

),

actual as (

    {%- for source_name, table_name in raw_tables %}
    select '{{ table_name }}' as entity, _reference_date::date as reference_date, count(*) as rows_in_raw
    from {{ source(source_name, table_name) }}
    group by 2
    {% if not loop.last %}union all{% endif %}
    {%- endfor %}

)

select
    coalesce(e.reference_date, a.reference_date)    as reference_date,
    coalesce(e.entity, a.entity)                    as entity,
    e.rows_loaded,
    a.rows_in_raw
from expected as e
full outer join actual as a using (reference_date, entity)
where e.rows_loaded is distinct from a.rows_in_raw
