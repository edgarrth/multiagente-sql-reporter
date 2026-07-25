#!/usr/bin/env sh
set -eu

COMPOSE_FILE="${COMPOSE_FILE:-infrastructure/docker-compose.yml}"
MODELS="${OLLAMA_MODELS:-qwen3:8b}"

echo "Starting Ollama..."
docker compose -f "$COMPOSE_FILE" --profile ollama up -d ollama

for model in $MODELS; do
  echo "Pulling $model..."
  docker compose -f "$COMPOSE_FILE" exec -T ollama ollama pull "$model"
done

echo "Available Ollama models:"
docker compose -f "$COMPOSE_FILE" exec -T ollama ollama list
