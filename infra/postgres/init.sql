-- Runs once, on the first start of an empty Postgres volume.
-- Schema-per-layer keeps responsibilities and permissions separated.

CREATE SCHEMA IF NOT EXISTS raw;          -- Bronze: 1:1 copy of the lake (loader)
CREATE SCHEMA IF NOT EXISTS staging;      -- Silver: dbt stg_ models (views)
CREATE SCHEMA IF NOT EXISTS intermediate; -- Silver: dbt int_ models (business rules, masking)
CREATE SCHEMA IF NOT EXISTS marts;        -- Gold:   dbt star schema (fct_ / dim_)
CREATE SCHEMA IF NOT EXISTS audit;        -- load control: which batches were loaded and when

COMMENT ON SCHEMA raw IS 'Bronze layer: untransformed data loaded from s3://credit-risk-lake/raw';
COMMENT ON SCHEMA marts IS 'Gold layer: star schema consumed by the credit decision engine and BI';

-- Read-only role for consumers (Streamlit, BI tools): least privilege.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_reader') THEN
        CREATE ROLE analytics_reader NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA marts TO analytics_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA marts GRANT SELECT ON TABLES TO analytics_reader;
