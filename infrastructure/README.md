# Infrastructure

`docker-compose.yml` starts PostgreSQL, Redis, FastAPI and Streamlit. Microsoft Teams is an
optional profile. Ollama is intentionally **not** started in Docker: the API connects to the
Ollama installation running on the host through `host.docker.internal`.

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
```

Teams remains isolated:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile teams up --build -d
```

Configure the host Ollama endpoint in `.env`:

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
```

The Compose service adds this portable mapping for Linux Docker Engine and WSL environments:

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

The PostgreSQL initialization scripts create operational, analytics and semantic layers plus an
`agent_reader` role whose permissions are limited to `SELECT` on the `semantic` schema.
