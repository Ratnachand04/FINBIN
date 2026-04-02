#!/usr/bin/env powershell

# Start Celery worker for fine-tuning tasks (Windows/PowerShell)
# Usage: .\scripts\start_celery_worker.ps1

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSScriptRoot
Set-Location $scriptDir

Write-Host "Starting Celery worker for fine-tuning tasks..."

# Activate environment if needed (uncomment if using venv)
# & .\venv\Scripts\Activate.ps1

# Start Celery worker with concurrency=1 (single task at a time)
celery -A backend.workers.finetune_worker worker `
  --loglevel=info `
  --concurrency=1 `
  -n "finetune_worker@localhost" `
  --prefetch-multiplier=1 `
  --max-tasks-per-child=10 `
  --time-limit=86400 `
  --soft-time-limit=82800
