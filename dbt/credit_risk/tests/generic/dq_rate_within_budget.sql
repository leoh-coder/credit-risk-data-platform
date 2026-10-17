{#- Fails with the measured rate when the share of rows matching `bad_rows` exceeds `max_pct` percent. -#}
{% test dq_rate_within_budget(model, bad_rows, max_pct) %}

with stats as (

    select
        count(*)                                                as total_rows,
        sum(case when {{ bad_rows }} then 1 else 0 end)         as bad_rows
    from {{ model }}

)

select
    total_rows,
    bad_rows,
    round(100.0 * bad_rows / nullif(total_rows, 0), 2)  as bad_pct,
    {{ max_pct }}                                       as max_pct
from stats
where 100.0 * bad_rows / nullif(total_rows, 0) > {{ max_pct }}

{% endtest %}
