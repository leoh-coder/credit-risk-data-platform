{#-
    Clear-text CPF, e-mail or mobile numbers in any text column; one row per (column, pattern) with matches.
    Columns are discovered at run time, so new columns are covered automatically.
-#}
{% test no_pii_patterns(model) %}

{%- set patterns = {
    'cpf': '[0-9]{3}[.][0-9]{3}[.][0-9]{3}-[0-9]{2}|^[0-9]{11}$',
    'email': '[^@[:space:]]+@[^@[:space:]]+[.][a-z]{2,}',
    'phone': '[0-9]{2}[) ]+9[0-9]{4}-[0-9]{4}',
} -%}

{%- set text_columns = [] -%}
{%- if execute -%}
    {%- for column in adapter.get_columns_in_relation(model) -%}
        {%- if column.is_string() -%}
            {%- do text_columns.append(column.name) -%}
        {%- endif -%}
    {%- endfor -%}
{%- endif -%}

{%- if text_columns | length == 0 %}
select null::text as column_name, null::text as pii_type, 0 as matches where false
{%- else -%}
    {%- for column_name in text_columns -%}
        {%- set outer = loop -%}
        {%- for pii_type, pattern in patterns.items() %}
select
    '{{ column_name }}' as column_name,
    '{{ pii_type }}'    as pii_type,
    count(*)            as matches
from {{ model }}
where {{ adapter.quote(column_name) }}::text ~ '{{ pattern }}'
having count(*) > 0
{% if not (outer.last and loop.last) %}union all{% endif %}
        {%- endfor -%}
    {%- endfor -%}
{%- endif %}

{% endtest %}
