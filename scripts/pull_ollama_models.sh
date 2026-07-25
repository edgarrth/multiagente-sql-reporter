#!/usr/bin/env sh
set -eu

MODELS="${OLLAMA_MODELS:-qwen3:8b}"
OLLAMA_BASE_URL="${OLLAMA_HOST_API_URL:-http://localhost:11434}"

if command -v ollama >/dev/null 2>&1; then
  for model in $MODELS; do
    echo "Pulling $model with the host Ollama CLI..."
    ollama pull "$model"
  done
  echo "Available host Ollama models:"
  ollama list
  exit 0
fi

echo "The Ollama CLI was not found on PATH; using the host HTTP API at $OLLAMA_BASE_URL."
for model in $MODELS; do
  echo "Pulling $model..."
  curl --fail --silent --show-error \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"stream\":false}" \
    "$OLLAMA_BASE_URL/api/pull"
  echo
done
curl --fail --silent --show-error "$OLLAMA_BASE_URL/api/tags"
echo
