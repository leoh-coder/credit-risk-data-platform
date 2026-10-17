-- Read-only login roles for Gold consumers; idempotent. Runs after 01-init.sql on a fresh volume, or via
-- `make roles`. Passwords come from the container environment.

\getenv ml_reader_password ML_READER_PASSWORD
\getenv dashboard_reader_password DASHBOARD_READER_PASSWORD
-- readable failure (the cast error quotes the message) instead of a syntax error
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
