# Portability

The warehouse targets PostgreSQL 17, and the SQL is not fully portable. Where a dbt or `dbt_utils`
macro exists, the models use it:

- `dbt_utils.date_spine` instead of `generate_series`;
- `dbt.dateadd` instead of `make_interval`;
- `SUM(CASE WHEN ...)` instead of `FILTER (WHERE ...)`;
- `ROW_NUMBER() OVER (...)` instead of `DISTINCT ON`.

What is still Postgres-specific, and the usual Amazon Redshift equivalent:

| Postgres construct | Where | Redshift equivalent |
|---|---|---|
| `jsonb`, `->>`, `jsonb_each` / `jsonb_each_text` with `CROSS JOIN LATERAL` | `audit.lake_loads` (`rows_loaded`, `manifest_entities`), `dq_ingestion_daily`, `dq_pipeline_loads`, `assert_raw_matches_latest_lake_load` | A `SUPER` column navigated with PartiQL, and unnesting/unpivoting of the object instead of `jsonb_each` |
| `sha256()` + `convert_to()` + `encode(..., 'hex')` | `stg_core_banking__customers` (CPF hash) | `SHA2(expr, 256)` |
| `age()` with `date_part('year', ...)` | `dim_customers` (age at registration) | `DATEDIFF(year, ...)` plus a birthday adjustment (no `AGE`) |
| `extract(isodow ...)` | `dim_date` | `DATE_PART(dow, ...)` (Sunday = 0), remapped to ISO |
| Regex class `\D` and the `'g'` flag in `regexp_replace` | `stg_core_banking__customers` | An explicit class such as `[^0-9]` and no flag: Redshift replaces every match by default, and its fourth argument is a position |
| `interval '...'` literals in date arithmetic | `int_customers__open_finance_features` (history window), `dim_date` (calendar end bound, month end) | `dbt.dateadd`, as the other models already use, or `DATEADD` |
| Enforced `PRIMARY KEY` from dbt contracts | every mart | Redshift enforces `NOT NULL` only; primary keys are informational, so the dbt `unique` tests remain the real guard |
| `text` columns in the mart contracts (`data_type: text`) | every mart | Redshift stores `TEXT` as `VARCHAR(256)`: declare explicit `VARCHAR` lengths |
| `uuid` type and `CREATE INDEX` in the loader DDL | `audit.lake_loads.load_id`, `raw.<entity>(_reference_date)` | `CHAR(36)` for the id; no indexes on Redshift, a sort key on `_reference_date` instead |
| `DO $$ ... $$` block, `NOLOGIN` role | `infra/postgres/init.sql` | No anonymous blocks: plain `CREATE ROLE` / groups and a stored procedure if needed |
| psql meta-commands (`\getenv`, `\gexec`, `\if`) | `infra/postgres/roles.sql` | Plain `CREATE USER ... PASSWORD` and `GRANT ROLE` run by the deployment tool |
| Loading through DuckDB's `postgres` extension (wire protocol) | `loader/` | `COPY` from the Parquet files already in S3; row inserts do not scale on Redshift |

This list comes from reading the code, not from running on Redshift. A real port would start by
running `dbt build` against a Redshift target.
