#!/usr/bin/env bash

set -euo pipefail

TRAIN=false
FINE_TUNE=false
TRAIN_SYMBOLS="BTCUSDT,ETHUSDT,DOGEUSDT"
TRAIN_INTERVAL="15m"
TRAIN_ROWS=6000
SENTIMENT_SAMPLES=30
ADAPTER_NAME="binfin-mistral-qlora"
OLLAMA_FINETUNED_MODEL_NAME="binfin-mistral-finance"
FINE_TUNE_DATASET_LIMIT=15000
FINE_TUNE_EPOCHS=1.0
FINE_TUNE_LR=0.0002
FINE_TUNE_BATCH_SIZE=1
FINE_TUNE_GRAD_ACCUM=16
FINE_TUNE_TRAINER_MODE="${FINETUNE_TRAINER_MODE:-gpu-qlora}"
MODEL_BOOTSTRAP_NAME="${OLLAMA_MODEL:-mistral:7b-instruct-q4_K_M}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --train)
      TRAIN=true
      shift
      ;;
    --fine-tune)
      FINE_TUNE=true
      shift
      ;;
    --symbols)
      TRAIN_SYMBOLS="$2"
      shift 2
      ;;
    --interval)
      TRAIN_INTERVAL="$2"
      shift 2
      ;;
    --rows)
      TRAIN_ROWS="$2"
      shift 2
      ;;
    --sentiment-samples)
      SENTIMENT_SAMPLES="$2"
      shift 2
      ;;
    --adapter-name)
      ADAPTER_NAME="$2"
      shift 2
      ;;
    --ollama-finetuned-model-name)
      OLLAMA_FINETUNED_MODEL_NAME="$2"
      shift 2
      ;;
    --fine-tune-dataset-limit)
      FINE_TUNE_DATASET_LIMIT="$2"
      shift 2
      ;;
    --fine-tune-epochs)
      FINE_TUNE_EPOCHS="$2"
      shift 2
      ;;
    --fine-tune-lr)
      FINE_TUNE_LR="$2"
      shift 2
      ;;
    --fine-tune-batch-size)
      FINE_TUNE_BATCH_SIZE="$2"
      shift 2
      ;;
    --fine-tune-grad-accum)
      FINE_TUNE_GRAD_ACCUM="$2"
      shift 2
      ;;
    --fine-tune-trainer-mode)
      FINE_TUNE_TRAINER_MODE="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [[ "$FINE_TUNE_TRAINER_MODE" != "gpu-qlora" && "$FINE_TUNE_TRAINER_MODE" != "cpu-lora" ]]; then
  echo "Invalid --fine-tune-trainer-mode: $FINE_TUNE_TRAINER_MODE (expected gpu-qlora or cpu-lora)"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required"
  exit 1
fi

wait_for_http() {
  local url="$1"
  local attempts="${2:-60}"
  local delay="${3:-2}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$delay"
  done

  return 1
}

bootstrap_ollama_model() {
  local model_name="$1"
  local active_modelfile="/models/active/Modelfile"

  echo "Preparing Ollama model: ${model_name}"
  if docker compose exec -T ollama sh -lc "test -f ${active_modelfile}"; then
    echo "Found /models/active/Modelfile. Creating replaceable model '${model_name}'..."
    docker compose exec -T ollama sh -lc "ollama create ${model_name} -f ${active_modelfile}"
  else
    echo "No /models/active/Modelfile found. Pulling base model '${model_name}'..."
    docker compose exec -T ollama sh -lc "ollama pull ${model_name}"
  fi
}

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update API keys in .env for data ingestion/training."
fi

echo "Pulling all images (frontend, backend, infra, local LLM)..."
docker compose -f docker-compose.yml pull

DEPLOYED_WITH_GPU=false
if [[ -f docker-compose.gpu.yml ]]; then
  echo "Trying GPU deployment for Ollama first..."
  if docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build; then
    DEPLOYED_WITH_GPU=true
  else
    echo "GPU deployment failed. Falling back to CPU deployment."
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml down || true
  fi
fi

if [[ "$DEPLOYED_WITH_GPU" != true ]]; then
  echo "Starting deployment with CPU-compatible Ollama..."
  docker compose -f docker-compose.yml up -d --build
fi

if ! wait_for_http "http://localhost:8000/api/v1/health/" 90 2; then
  echo "Backend health check failed at http://localhost:8000/api/v1/health/"
  exit 1
fi

if ! wait_for_http "http://localhost:8501" 90 2; then
  echo "Frontend health check failed at http://localhost:8501"
  exit 1
fi

echo "Ensuring Mistral model is available in Ollama..."
bootstrap_ollama_model "$MODEL_BOOTSTRAP_NAME"

if [[ "$TRAIN" == true ]]; then
  echo "Triggering finance-news training pipeline..."
  IFS=',' read -r -a SYMBOLS <<< "$TRAIN_SYMBOLS"
  JSON_SYMBOLS="$(printf '"%s",' "${SYMBOLS[@]}")"
  JSON_SYMBOLS="[${JSON_SYMBOLS%,}]"

  PAYLOAD=$(cat <<EOF
{
  "symbols": $JSON_SYMBOLS,
  "interval": "$TRAIN_INTERVAL",
  "max_rows_per_symbol": $TRAIN_ROWS,
  "sentiment_sample_size": $SENTIMENT_SAMPLES
}
EOF
)

  curl -fsS -X POST "http://localhost:8000/api/v1/model/train-finance-news" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD"
  echo
fi

if [[ "$FINE_TUNE" == true ]]; then
  FINE_TUNE_SCRIPT="$ROOT_DIR/scripts/run_llm_finetune.sh"
  if [[ ! -f "$FINE_TUNE_SCRIPT" ]]; then
    echo "Fine-tune script not found: $FINE_TUNE_SCRIPT"
    exit 1
  fi

  echo "Running full QLoRA fine-tuning pipeline..."
  bash "$FINE_TUNE_SCRIPT" \
    --adapter-name "$ADAPTER_NAME" \
    --ollama-model-name "$OLLAMA_FINETUNED_MODEL_NAME" \
    --trainer-mode "$FINE_TUNE_TRAINER_MODE" \
    --dataset-limit "$FINE_TUNE_DATASET_LIMIT" \
    --epochs "$FINE_TUNE_EPOCHS" \
    --lr "$FINE_TUNE_LR" \
    --batch-size "$FINE_TUNE_BATCH_SIZE" \
    --grad-accum "$FINE_TUNE_GRAD_ACCUM"
fi

echo "Deployment complete."
echo "Backend:    http://localhost:8000"
echo "Frontend:   http://localhost:8501"
echo "Ollama:     http://localhost:11434"
echo "Prometheus: http://localhost:9090"
echo "Grafana:    http://localhost:3000"
echo "GPU mode:   $DEPLOYED_WITH_GPU"
echo "RAG mode:   cpu-context + ollama-generation"
echo "Fine-tune:  $FINE_TUNE"
echo "Trainer:    $FINE_TUNE_TRAINER_MODE"
