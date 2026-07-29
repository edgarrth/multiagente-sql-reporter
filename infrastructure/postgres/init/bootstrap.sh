#!/bin/sh
set -eu

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_PORT:?POSTGRES_PORT is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${CONTROL_DATABASE:?CONTROL_DATABASE is required}"
: "${BUSINESS_DATABASE:?BUSINESS_DATABASE is required}"
: "${AGENT_READER_USER:?AGENT_READER_USER is required}"
: "${AGENT_READER_PASSWORD:?AGENT_READER_PASSWORD is required}"
: "${AGENT_STATEMENT_TIMEOUT:?AGENT_STATEMENT_TIMEOUT is required}"
: "${BUSINESS_DATA_MODE:?BUSINESS_DATA_MODE is required}"
: "${BUSINESS_TIMEZONE:?BUSINESS_TIMEZONE is required}"
: "${BOOTSTRAP_SCHEMA_VERSION:?BOOTSTRAP_SCHEMA_VERSION is required}"
: "${POSTGRES_BOOTSTRAP_RETRY_SECONDS:?POSTGRES_BOOTSTRAP_RETRY_SECONDS is required}"
: "${REFRESH_BUSINESS_DATA_ON_START:?REFRESH_BUSINESS_DATA_ON_START is required}"

export PGPASSWORD="$POSTGRES_PASSWORD"

until pg_isready \
  -h "$POSTGRES_HOST" \
  -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" >/dev/null 2>&1; do
  echo "Waiting for PostgreSQL admin database..."
  sleep "$POSTGRES_BOOTSTRAP_RETRY_SECONDS"
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
  -v agent_statement_timeout="$AGENT_STATEMENT_TIMEOUT" \
  -v create_business="$CREATE_BUSINESS" \
  -h "$POSTGRES_HOST" \
  -p "$POSTGRES_PORT" \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  -f /bootstrap/00-roles-and-databases.sql

psql \
  -v ON_ERROR_STOP=1 \
  -v owner_role="$POSTGRES_USER" \
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
  -v owner_role="$POSTGRES_USER" \
  -v reader_role="$AGENT_READER_USER" \
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
    -v business_timezone="$BUSINESS_TIMEZONE" \
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
    -v reader_role="$AGENT_READER_USER" \
    -v business_timezone="$BUSINESS_TIMEZONE" \
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
