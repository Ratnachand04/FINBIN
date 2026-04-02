#!/usr/bin/env bash

set -euo pipefail

ADAPTER_NAME="binfin-mistral-qlora"
OLLAMA_MODEL_NAME="binfin-mistral-finance"
TRAINER_MODE="gpu-qlora"
DATASET_LIMIT=15000
EPOCHS=1.0
LEARNING_RATE=0.0002
BATCH_SIZE=1
GRAD_ACCUM=16

while [[ $# -gt 0 ]]; do
  case "$1" in
    --adapter-name)
      ADAPTER_NAME="$2"
      shift 2
      ;;
    --ollama-model-name)
      OLLAMA_MODEL_NAME="$2"
      shift 2
      ;;
    --trainer-mode)
      TRAINER_MODE="$2"
      shift 2
      ;;
    --dataset-limit)
      DATASET_LIMIT="$2"
      shift 2
      ;;
    --epochs)
      EPOCHS="$2"
      shift 2
      ;;
    --lr)
      LEARNING_RATE="$2"
      shift 2
      ;;
    --batch-size)
      BATCH_SIZE="$2"
      shift 2
      ;;
    --grad-accum)
      GRAD_ACCUM="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

case "$TRAINER_MODE" in
  gpu-qlora|cpu-lora)
    ;;
  *)
    echo "Invalid --trainer-mode: $TRAINER_MODE (expected gpu-qlora or cpu-lora)"
    exit 1
    ;;
esac

TRAIN_SCRIPT="train_qlora.py"
if [[ "$TRAINER_MODE" == "cpu-lora" ]]; then
  TRAIN_SCRIPT="train_lora_cpu.py"
fi

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

echo "Starting core stack services needed for fine-tuning..."
docker compose up -d postgres ollama

echo "Exporting SFT dataset from PostgreSQL..."
docker compose --profile llm-train run --rm llm-trainer python3 export_dataset.py --limit "$DATASET_LIMIT"

echo "Running $TRAINER_MODE training for Mistral..."
docker compose --profile llm-train run --rm llm-trainer python3 "$TRAIN_SCRIPT" \
  --adapter-name "$ADAPTER_NAME" \
  --epochs "$EPOCHS" \
  --learning-rate "$LEARNING_RATE" \
  --batch-size "$BATCH_SIZE" \
  --grad-accum "$GRAD_ACCUM"

ADAPTER_DIR="$ROOT_DIR/llm_trainer/artifacts/output/adapters/$ADAPTER_NAME"
if [[ ! -d "$ADAPTER_DIR" ]]; then
  echo "Adapter directory not found: $ADAPTER_DIR"
  exit 1
fi

echo "Generating Ollama Modelfile..."
TMP_DIR="/tmp/binfin_${TRAINER_MODE//-/_}_${ADAPTER_NAME}"
MODEFILE_PATH="$ROOT_DIR/llm_trainer/artifacts/ollama/Modelfile"

echo "Copying adapter and Modelfile to Ollama container..."
docker compose exec -T ollama sh -lc "mkdir -p $TMP_DIR"
docker compose cp "$ADAPTER_DIR" "ollama:$TMP_DIR/adapter"
docker compose --profile llm-train run --rm llm-trainer python3 package_ollama.py \
  --adapter-dir "$TMP_DIR/adapter" \
  --model-name "$OLLAMA_MODEL_NAME"
docker compose cp "$MODEFILE_PATH" "ollama:$TMP_DIR/Modelfile"

echo "Building fine-tuned model in Ollama..."
docker compose exec -T ollama sh -lc "ollama pull mistral:7b-instruct-q4_K_M ; ollama create $OLLAMA_MODEL_NAME -f $TMP_DIR/Modelfile"

echo "Fine-tuned model is ready in Ollama: $OLLAMA_MODEL_NAME (trainer: $TRAINER_MODE)"
