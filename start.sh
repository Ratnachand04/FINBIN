#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x "scripts/deploy_model.sh" ]]; then
  chmod +x scripts/deploy_model.sh
fi

bash scripts/deploy_model.sh "$@"
