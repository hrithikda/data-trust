{#- The snapshot date as a SQL date literal. All "as of today" logic goes through this macro. -#}
{% macro reference_date() -%}
    date '{{ var("reference_date") }}'
{%- endmacro %}
