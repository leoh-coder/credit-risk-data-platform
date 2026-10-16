{#- Kimball "unknown member": facts point here when the dimension row is missing (e.g. quarantined parent). -#}
{% macro unknown_key() -%}
    '-1'
{%- endmacro %}

{#- Smart integer date key (YYYYMMDD): readable, sortable, and stable across rebuilds. NULL stays NULL. -#}
{% macro date_key(column) -%}
    to_char({{ column }}, 'YYYYMMDD')::int
{%- endmacro %}
