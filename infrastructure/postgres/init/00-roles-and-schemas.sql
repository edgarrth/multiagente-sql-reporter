DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader') THEN
    CREATE ROLE agent_reader LOGIN PASSWORD 'agent_readonly'
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;

ALTER ROLE agent_reader SET default_transaction_read_only = on;
ALTER ROLE agent_reader SET statement_timeout = '20s';
ALTER ROLE agent_reader SET search_path = semantic, pg_catalog;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS operational;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS semantic;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA operational, analytics, app FROM agent_reader;
GRANT USAGE ON SCHEMA semantic TO agent_reader;
GRANT CONNECT ON DATABASE axiz_sql_agent TO agent_reader;
