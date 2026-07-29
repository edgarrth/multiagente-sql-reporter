CREATE SCHEMA IF NOT EXISTS app AUTHORIZATION :"owner_role";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS app.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username varchar(100) NOT NULL UNIQUE,
  password_hash text,
  external_id text,
  roles text[] NOT NULL DEFAULT ARRAY['analyst'],
  auth_source varchar(30) NOT NULL DEFAULT 'local',
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (external_id, auth_source)
);

CREATE TABLE IF NOT EXISTS app.chat_sessions (
  id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES app.users(id),
  title varchar(200) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.chat_messages (
  id bigserial PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES app.chat_sessions(id) ON DELETE CASCADE,
  role varchar(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.agent_runs (
  id uuid PRIMARY KEY,
  session_id uuid NOT NULL REFERENCES app.chat_sessions(id),
  user_id uuid NOT NULL REFERENCES app.users(id),
  question text NOT NULL,
  status varchar(40) NOT NULL,
  state jsonb NOT NULL DEFAULT '{}'::jsonb,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS app.session_memory (
  session_id uuid PRIMARY KEY REFERENCES app.chat_sessions(id) ON DELETE CASCADE,
  memory jsonb NOT NULL DEFAULT '{}'::jsonb,
  revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
  last_run_id uuid REFERENCES app.agent_runs(id) ON DELETE SET NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.human_feedback (
  id bigserial PRIMARY KEY,
  run_id uuid NOT NULL REFERENCES app.agent_runs(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES app.users(id),
  decision varchar(30) NOT NULL,
  comment text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.audit_events (
  id bigserial PRIMARY KEY,
  run_id uuid REFERENCES app.agent_runs(id) ON DELETE SET NULL,
  user_id uuid REFERENCES app.users(id) ON DELETE SET NULL,
  event_type varchar(100) NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.channel_sessions (
  channel varchar(30) NOT NULL,
  conversation_id text NOT NULL,
  user_id uuid NOT NULL REFERENCES app.users(id),
  session_id uuid NOT NULL REFERENCES app.chat_sessions(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (channel, conversation_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_created
  ON app.chat_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_user_created
  ON app.agent_runs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_memory_updated
  ON app.session_memory(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_run_created
  ON app.audit_events(run_id, created_at);

-- Resilience, idempotency and distributed execution leases (0.6.0).
ALTER TABLE app.agent_runs
  ADD COLUMN IF NOT EXISTS idempotency_key varchar(128),
  ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS lease_owner varchar(100),
  ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
  ADD COLUMN IF NOT EXISTS heartbeat_at timestamptz,
  ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz,
  ADD COLUMN IF NOT EXISTS started_at timestamptz;

ALTER TABLE app.human_feedback
  ADD COLUMN IF NOT EXISTS idempotency_key varchar(128),
  ADD COLUMN IF NOT EXISTS run_version integer;

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_user_idempotency
  ON app.agent_runs(user_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_runs_active_lease
  ON app.agent_runs(status, lease_expires_at)
  WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_agent_runs_session_status
  ON app.agent_runs(session_id, status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_human_feedback_run_idempotency
  ON app.human_feedback(run_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
