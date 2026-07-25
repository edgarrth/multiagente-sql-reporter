# Infrastructure

`docker-compose.yml` starts PostgreSQL, Redis, FastAPI and Streamlit. Microsoft Teams and Ollama
are optional profiles, so a failure or absence of either integration does not stop the main UI.

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
```

Teams remains isolated:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile teams up --build -d
```

Ollama is also isolated:

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  --profile ollama up --build -d
```

Pull a lightweight local model:

```bash
OLLAMA_MODELS="qwen3:8b" ./scripts/pull_ollama_models.sh
```

Pull the larger SQL-oriented model only on a host with sufficient RAM/VRAM:

```bash
OLLAMA_MODELS="qwen3-coder:30b" ./scripts/pull_ollama_models.sh
```

The PostgreSQL initialization scripts create operational, analytics and semantic layers plus an
`agent_reader` role whose permissions are limited to `SELECT` on the `semantic` schema.
