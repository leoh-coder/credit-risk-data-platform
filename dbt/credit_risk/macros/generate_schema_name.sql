{#-
    dbt's default concatenates target schema + custom schema ("staging_marts").
    The warehouse already has one schema per layer (infra/postgres/init.sql) with its own grants,
    so models land in exactly the schema they declare.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
