{#- Unknown member: facts point here when the dimension row is missing (e.g. a quarantined parent). -#}
{% macro unknown_key() -%}
    '-1'
{%- endmacro %}

{#- Integer date key (YYYYMMDD): readable and stable across rebuilds. NULL stays NULL. -#}
{% macro date_key(column) -%}
    to_char({{ column }}, 'YYYYMMDD')::int
{%- endmacro %}
