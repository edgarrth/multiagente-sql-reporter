#!/bin/sh
set -eu

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-app_owner}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-app_owner}"
CONTROL_DATABASE="${CONTROL_DATABASE:-axiz_agent_control}"
BUSINESS_DATABASE="${BUSINESS_DATABASE:-axiz_business_data}"
AGENT_READER_USER="${AGENT_READER_USER:-agent_reader}"
AGENT_READER_PASSWORD="${AGENT_READER_PASSWORD:-agent_readonly}"
BUSINESS_DATA_MODE="${BUSINESS_DATA_MODE:-embedded}"
BOOTSTRAP_SCHEMA_VERSION="${BOOTSTRAP_SCHEMA_VERSION:-0.4.2}"
REFRESH_BUSINESS_DATA_ON_START="${REFRESH_BUSINESS_DATA_ON_START:-false}"

export PGPASSWORD="$POSTGRES_PASSWORD"

until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d postgres >/dev/null 2>&1; do
  echo "Waiting for PostgreSQL admin database..."
  sleep 2
done

CREATE_BUSINESS=false
if [ "$BUSINESS_DATA_MODE" = "embedded" ]; then
  CREATE_BUSINESS=true
fi

psql \
  -v ON_ERROR_STOP=1 \
  -v control_db="$CONTROL_DATABASE" \
  -v business_db="$BUSINESS_DATABASE" \
  -v owner_role="$POSTGRES_USER" \
  -v reader_role="$AGENT_READER_USER" \
  -v reader_password="$AGENT_READER_PASSWORD" \
  -v create_business="$CREATE_BUSINESS" \
  -h "$POSTGRES_HOST" \
  -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" \
  -d postgres \
  -f /bootstrap/00-roles-and-databases.sql

psql \
  -v ON_ERROR_STOP=1 \
  -h "$POSTGRES_HOST" \
  -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" \
  -d "$CONTROL_DATABASE" \
  -f /bootstrap/01-app-tables.sql

if [ "$BUSINESS_DATA_MODE" != "embedded" ]; then
  echo "BUSINESS_DATA_MODE=$BUSINESS_DATA_MODE: skipping embedded business-data initialization."
  exit 0
fi

psql \
  -v ON_ERROR_STOP=1 \
  -h "$POSTGRES_HOST" \
  -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" \
  -d "$BUSINESS_DATABASE" \
  -f /bootstrap/02-operational-model.sql

TRANSACTION_COUNT="$(
  psql -At \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$BUSINESS_DATABASE" \
    -c "SELECT count(*) FROM operational.payment_transactions;"
)"

if [ "$TRANSACTION_COUNT" = "0" ]; then
  echo "Loading the synthetic business dataset..."
  psql \
    -v ON_ERROR_STOP=1 \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$BUSINESS_DATABASE" \
    -f /bootstrap/03-seed-data.sql
else
  echo "Synthetic dataset already exists ($TRANSACTION_COUNT transactions); seed skipped."
fi

CURRENT_SCHEMA_VERSION="$(
  psql -At \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$BUSINESS_DATABASE" \
    -c "SELECT value FROM public.axiz_bootstrap_metadata WHERE key = 'business_schema_version';" \
    2>/dev/null || true
)"

if [ "$REFRESH_BUSINESS_DATA_ON_START" = "true" ] || [ "$CURRENT_SCHEMA_VERSION" != "$BOOTSTRAP_SCHEMA_VERSION" ]; then
  echo "Building analytics and semantic layers for schema version $BOOTSTRAP_SCHEMA_VERSION..."
  psql \
    -v ON_ERROR_STOP=1 \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$BUSINESS_DATABASE" \
    -f /bootstrap/04-analytics-semantic.sql

  psql \
    -v ON_ERROR_STOP=1 \
    -v schema_version="$BOOTSTRAP_SCHEMA_VERSION" \
    -h "$POSTGRES_HOST" \
    -p "$POSTGRES_PORT" \
    -U "$POSTGRES_USER" \
    -d "$BUSINESS_DATABASE" <<'SQL'
INSERT INTO public.axiz_bootstrap_metadata(key, value, updated_at)
VALUES ('business_schema_version', :'schema_version', now())
ON CONFLICT (key)
DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;
SQL
else
  echo "Analytics and semantic layers are already at version $CURRENT_SCHEMA_VERSION."
fi

CONTROL_READY="$(psql -At -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$CONTROL_DATABASE" -c "SELECT to_regclass('app.chat_sessions') IS NOT NULL AND to_regclass('app.session_memory') IS NOT NULL;")"
BUSINESS_READY="$(psql -At -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$BUSINESS_DATABASE" -c "SELECT to_regclass('semantic.v_payment_transactions') IS NOT NULL;")"

if [ "$CONTROL_READY" != "t" ] || [ "$BUSINESS_READY" != "t" ]; then
  echo "Bootstrap validation failed: control=$CONTROL_READY business=$BUSINESS_READY" >&2
  exit 1
fi

echo "Axiz PostgreSQL bootstrap completed successfully."
