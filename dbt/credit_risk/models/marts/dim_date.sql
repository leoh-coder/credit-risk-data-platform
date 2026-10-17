{#-
    Calendar from the first contract year to the last due-date year. dbt_utils.date_spine instead of
    generate_series, which Redshift does not support on data tables.
-#}

{%- set bounds_query -%}
    select
        date_trunc('year', least(
            (select min(contract_date) from {{ ref('stg_core_banking__credit_operations') }}),
            (select min(due_date) from {{ ref('stg_core_banking__installments') }})
        ))::date                                                        as start_date,
        -- date_spine's end_date is exclusive: January 1st after the last year covered
        (date_trunc('year', greatest(
            (select max(due_date) from {{ ref('stg_core_banking__installments') }}),
            (select max(payment_date) from {{ ref('stg_core_banking__installments') }})
        )) + interval '1 year')::date                                   as end_date
{%- endset -%}

{%- if execute -%}
    {%- set bounds = run_query(bounds_query).rows[0] -%}
    {%- set start_date, end_date = bounds[0], bounds[1] -%}
{%- else -%}
    {%- set start_date, end_date = '2000-01-01', '2000-01-02' -%}
{%- endif -%}

with spine as (

    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ start_date ~ "' as date)",
        end_date="cast('" ~ end_date ~ "' as date)"
    ) }}

),

calendar as (

    select cast(date_day as date) as calendar_date
    from spine

)

select
    {{ date_key('calendar_date') }}                         as date_key,
    calendar_date,
    extract(year from calendar_date)::int                   as year,
    extract(quarter from calendar_date)::int                as quarter,
    extract(month from calendar_date)::int                  as month,
    to_char(calendar_date, 'Mon')                           as month_name,
    to_char(calendar_date, 'YYYY-MM')                       as year_month,
    extract(day from calendar_date)::int                    as day_of_month,
    extract(isodow from calendar_date)::int                 as day_of_week,
    to_char(calendar_date, 'Dy')                            as day_name,
    extract(isodow from calendar_date) in (6, 7)            as is_weekend,
    calendar_date = (date_trunc('month', calendar_date) + interval '1 month - 1 day')::date
                                                            as is_month_end
from calendar
