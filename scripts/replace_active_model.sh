#!/usr/bin/env bash
set -euo pipefail

MODEL_NAME="${1:-binfin-mistral-finance}"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

ACTIVE_DIR="models/active"
MODEFILE_PATH="$ACTIVE_DIR/Modelfile"

if [[ ! -f "$MODEFILE_PATH" ]]; then
  echo "Missing $MODEFILE_PATH"
  echo "Create models/active/Modelfile first (for FROM <base model> or FROM <gguf path>)."
  exit 1
fi

echo "Ensuring Ollama service is running..."
docker compose up -d ollama

echo "Building active model '$MODEL_NAME' from $MODEFILE_PATH..."
docker compose exec -T ollama sh -lc "ollama create $MODEL_NAME -f /models/active/Modelfile"

echo "Model replacement complete. Active Ollama model: $MODEL_NAME"
