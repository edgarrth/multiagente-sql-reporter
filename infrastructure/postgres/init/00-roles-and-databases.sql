\set ON_ERROR_STOP on

-- The PoC uses two logical databases in one PostgreSQL instance:
--   axiz_agent_control: authentication, conversations, audit and LangGraph checkpoints.
--   axiz_business_data: operational, analytical and governed semantic data.
-- Production deployments can move either database to a separate managed service without
-- changing the agent workflow because the application already uses independent DSNs.

SELECT 'CREATE DATABASE axiz_business_data OWNER app_owner'
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = 'axiz_business_data'
)
\gexec

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

-- Control-plane isolation: the SQL agent must never connect to the session/checkpoint database.
REVOKE CONNECT ON DATABASE axiz_agent_control FROM PUBLIC;
GRANT CONNECT ON DATABASE axiz_agent_control TO app_owner;
REVOKE CONNECT ON DATABASE axiz_agent_control FROM agent_reader;

-- Data-plane isolation: only the owner and the read-only execution role can connect.
REVOKE CONNECT ON DATABASE axiz_business_data FROM PUBLIC;
GRANT CONNECT ON DATABASE axiz_business_data TO app_owner, agent_reader;

\connect axiz_agent_control
CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION app_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
