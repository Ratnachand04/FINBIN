#!/usr/bin/env bash

# Start Celery worker for fine-tuning tasks
# Usage: ./start_celery_worker.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "Starting Celery worker for fine-tuning tasks..."

# Activate environment if needed (uncomment if using venv)
# source venv/bin/activate

# Start Celery worker with concurrency=1 (single task at a time)
# -A: application module
# -l: log level
# -c: concurrency (number of parallel workers)
# --loglevel: log level
# -n: worker hostname/name
celery -A backend.workers.finetune_worker worker \
  --loglevel=info \
  --concurrency=1 \
  -n finetune_worker@%h \
  --prefetch-multiplier=1 \
  --max-tasks-per-child=10 \
  --time-limit=86400 \
  --soft-time-limit=82800
