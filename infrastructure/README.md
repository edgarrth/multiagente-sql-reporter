# Infrastructure

`docker-compose.yml` starts PostgreSQL 18, Redis 8, FastAPI and Streamlit. Microsoft Teams is an
optional profile. Ollama is intentionally **not** started in Docker: the API connects to the Ollama
installation running on the host through `host.docker.internal`.

# PostgreSQL databases

The PoC uses one PostgreSQL container and two independent logical databases:

| Database | Content | Connection |
|---|---|---|
| `axiz_agent_control` | Users, sessions, chat messages, runs, feedback, audit and LangGraph checkpoints | `DATABASE_URL` and `CHECKPOINT_DATABASE_URL` |
| `axiz_business_data` | `operational`, `analytics` and `semantic` schemas | `AGENT_DATABASE_URL` using `agent_reader` |

The `agent_reader` role cannot connect to `axiz_agent_control`. It can only connect to
`axiz_business_data` and execute `SELECT` against the `semantic` schema.

The initialization order is:

```text
00-roles-and-databases.sql   create both databases and the read-only role
01-app-tables.sql            create control-plane application tables
02-operational-model.sql     create source-like business tables
03-seed-data.sql             generate deterministic synthetic data
04-analytics-semantic.sql    build analytics tables and governed semantic views
```

In production, the control database and analytical platform should normally be separate managed
services. The code already uses independent DSNs, so the data plane can point to another PostgreSQL
instance or a future query-tool adapter without changing LangGraph.

# Start the stack

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
```

Teams remains isolated:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile teams up --build -d
```

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
