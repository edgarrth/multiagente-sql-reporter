#!/usr/bin/env sh
set -eu

HOST_URL="${OLLAMA_HOST_API_URL:-http://localhost:11434}"
CONTAINER_URL="${OLLAMA_BASE_URL:-http://host.docker.internal:11434}"

echo "Checking Ollama on the host: $HOST_URL"
curl --fail --silent --show-error "$HOST_URL/api/tags" >/dev/null
echo "Host Ollama API is reachable."

echo "Checking Ollama from the API container: $CONTAINER_URL"
docker compose --env-file .env -f infrastructure/docker-compose.yml exec -T api \
  python -c "import json, urllib.request; print(json.load(urllib.request.urlopen('${CONTAINER_URL}/api/tags', timeout=5)))"
