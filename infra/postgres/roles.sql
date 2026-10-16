-- Login roles for the consumers of the Gold layer. Idempotent:
--   fresh volume  -> runs automatically after 01-init.sql (docker-entrypoint-initdb.d)
--   existing one  -> `make roles`
-- Passwords come from the container environment; psql fails (ON_ERROR_STOP) if one is missing.
-- Both roles only inherit analytics_reader (USAGE + SELECT on marts) and default to read-only sessions.

\getenv ml_reader_password ML_READER_PASSWORD
\getenv dashboard_reader_password DASHBOARD_READER_PASSWORD
-- fail with a readable message (the cast error quotes it) instead of a SQL syntax error
\if :{?ml_reader_password}
\else
    select 'ML_READER_PASSWORD is not set in the postgres container'::int;
\endif
\if :{?dashboard_reader_password}
\else
    select 'DASHBOARD_READER_PASSWORD is not set in the postgres container'::int;
\endif

select format('create role %I login', role_name)
from (values ('ml_reader'), ('dashboard_reader')) as roles (role_name)
where not exists (select from pg_roles where rolname = role_name)
\gexec

select format('alter role ml_reader with login password %L', :'ml_reader_password')
\gexec
select format('alter role dashboard_reader with login password %L', :'dashboard_reader_password')
\gexec

grant analytics_reader to ml_reader, dashboard_reader;
alter role ml_reader set default_transaction_read_only = on;
alter role dashboard_reader set default_transaction_read_only = on;
