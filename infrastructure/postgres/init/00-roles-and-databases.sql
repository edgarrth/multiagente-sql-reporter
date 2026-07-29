\set ON_ERROR_STOP on

SELECT format('CREATE DATABASE %I OWNER %I', :'control_db', :'owner_role')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = :'control_db'
)
\gexec

SELECT format(
  'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
  :'reader_role',
  :'reader_password'
)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_roles WHERE rolname = :'reader_role'
)
\gexec

SELECT format('ALTER ROLE %I PASSWORD %L', :'reader_role', :'reader_password')
\gexec
SELECT format('ALTER ROLE %I SET default_transaction_read_only = on', :'reader_role')
\gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'reader_role', :'agent_statement_timeout')
\gexec
SELECT format('ALTER ROLE %I SET search_path = semantic, pg_catalog', :'reader_role')
\gexec

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'control_db')
\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'control_db', :'owner_role')
\gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM %I', :'control_db', :'reader_role')
\gexec

\if :create_business
SELECT format('CREATE DATABASE %I OWNER %I', :'business_db', :'owner_role')
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = :'business_db'
)
\gexec
SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', :'business_db')
\gexec
SELECT format(
  'GRANT CONNECT ON DATABASE %I TO %I, %I',
  :'business_db',
  :'owner_role',
  :'reader_role'
)
\gexec
\endif
