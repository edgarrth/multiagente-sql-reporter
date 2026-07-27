# Infrastructure

`docker-compose.yml` starts PostgreSQL 18, Redis 8, FastAPI and Streamlit. For the PoC, the same
PostgreSQL service creates both the control database and the complete synthetic business-data database.
Microsoft Teams is an optional profile. Ollama is intentionally **not** started in Docker: the API
connects to the Ollama installation running on the host through `host.docker.internal`.

# PostgreSQL databases

The PoC uses one PostgreSQL container and two independent logical databases:

| Database | Content | Connection |
|---|---|---|
| `axiz_agent_control` | Users, sessions, versioned structured memory, chat messages, runs, feedback, audit and LangGraph checkpoints | `DATABASE_URL` and `CHECKPOINT_DATABASE_URL` |
| `axiz_business_data` | `operational`, `analytics` and `semantic` schemas | `AGENT_DATABASE_URL` using `agent_reader` |

The `agent_reader` role cannot connect to `axiz_agent_control`. It can only connect to
`axiz_business_data` and execute `SELECT` against the `semantic` schema.

The initialization order is:

```text
00-roles-and-databases.sql   create both databases and the read-only role
01-app-tables.sql            create control-plane tables, including app.session_memory
02-operational-model.sql     create source-like business tables
03-seed-data.sql             generate deterministic synthetic data
04-analytics-semantic.sql    build analytics tables and governed semantic views
```

The default PoC mode is `BUSINESS_DATA_MODE=embedded`, with `AGENT_DATABASE_URL` pointing to
`postgres:5432/axiz_business_data`. In production, set `BUSINESS_DATA_MODE=external` and replace only
`AGENT_DATABASE_URL` with the managed data-platform endpoint. The code already uses independent DSNs,
so this change does not modify LangGraph or the agent implementations.

# Start the stack

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
```

Teams remains isolated:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile teams up --build -d
```

# Anthropic Claude

El servicio `api` instala el SDK oficial de Anthropic y recibe las variables del `.env` de la raíz.
Para usar Claude:

```dotenv
ANTHROPIC_API_KEY=<api-key>
ANTHROPIC_BASE_URL=https://api.anthropic.com
LLM_PROVIDER=anthropic
AXIZ_SQL_GENERATOR_MODEL_PRESET=anthropic_claude_sonnet_5_sql
```

Los demás agentes pueden configurarse individualmente con los presets Anthropic publicados en
`config/agents.yaml`. No agregues `temperature`, `top_p` ni `top_k` a los presets Claude 4.7+/5; el
adaptador los omite y usa `thinking`/`effort` solo cuando el modelo lo soporta.

# Ollama on the host

Configure the host Ollama endpoint in `.env`:

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

The Compose service adds this mapping for Linux Docker Engine and WSL environments:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

Verify connectivity from both the host and the API container:

```bash
make check-ollama
```

Pull models into the host installation, not into a Docker volume:

```bash
OLLAMA_MODELS="qwen3:8b" make pull-ollama
```

For native API execution outside Docker, use:

```dotenv
OLLAMA_BASE_URL=http://localhost:11434
```

On Linux, if Ollama only listens on loopback and the container cannot connect, configure the host
service with `OLLAMA_HOST=0.0.0.0:11434`, restart Ollama, and keep port 11434 protected by the host
firewall. Do not publish it to untrusted networks.


# Structured conversation memory

`app.session_memory` stores one bounded JSONB memory document per chat session. It is created idempotently on every bootstrap, so upgrading an existing PostgreSQL volume does not require deleting data. The row is removed automatically when its parent chat session is deleted.

The API persists only governed analytical context and a configurable result sample:

```dotenv
CONVERSATION_MEMORY_RESULT_SAMPLE_ROWS=5
```

This memory belongs to the control database. The `agent_reader` role used against `axiz_business_data` cannot connect to or read it.


## Query engine y resiliencia 0.6.0

La PoC usa `QUERY_ENGINE=postgres` y mantiene `axiz_business_data` dentro del mismo Compose. En producción se puede externalizar el DSN sin cambiar el workflow. El bootstrap agrega idempotentemente columnas de idempotencia, versión, lease, heartbeat y cancelación en el control plane.

La API valida modelos al iniciar según `MODEL_VALIDATION_MODE` y limita concurrencia mediante `RUN_LEASE_SECONDS`, `RUN_LEASE_HEARTBEAT_SECONDS`, `MAX_CONCURRENT_RUNS_PER_USER` y `MAX_CONCURRENT_LLM_CALLS`.

# Logs y diagnóstico

La API desactiva el access log genérico de Uvicorn y usa logs estructurados propios. Esto permite
correlacionar solicitudes, runs, tareas, llamadas LLM, validaciones de costo, ejecución SQL y
persistencia sin registrar prompts, respuestas del modelo ni SQL por defecto.

```dotenv
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_HTTP_REQUESTS=true
LOG_HEALTH_CHECKS=false
LOG_WORKFLOW_STAGES=true
LOG_LLM_CALLS=true
LOG_QUERY_EVENTS=true
LOG_SQL_TEXT=false
SSE_HEARTBEAT_SECONDS=15
STREAMLIT_RUN_RECOVERY_TIMEOUT_SECONDS=240
```

`LOG_HEALTH_CHECKS=false` silencia `/health/live` y `/health/ready` en los logs, pero mantiene los
endpoints y health checks activos. Para seguir la API:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml logs -f api
```

Para investigar una aprobación HITL que no muestre respuesta, busca el `run_id` y verifica estos
eventos en orden: `agent_resume_claimed`, `query_execution_completed`,
`agent_run_response_persisted` y `agent_terminal_message_persisted`.
