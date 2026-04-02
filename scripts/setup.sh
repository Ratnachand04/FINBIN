#!/bin/bash

set -e

echo "Crypto Intelligence Terminal - Setup Script"
echo "=========================================="

echo "Checking prerequisites..."
command -v python3 >/dev/null 2>&1 || { echo "Python 3.9+ required"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "Docker required"; exit 1; }
if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose required"
    exit 1
fi

echo "Creating Python virtual environment..."
python3 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Setting up environment variables..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Please edit .env file with your API keys"
    exit 1
fi

echo "Pulling Docker images..."
docker compose pull

echo "Starting infrastructure (PostgreSQL, Redis)..."
docker compose up -d postgres redis

echo "Waiting for services to be ready..."
sleep 10

echo "Running database migrations..."
POSTGRES_USER="${POSTGRES_USER:-binfin}"
POSTGRES_DB="${POSTGRES_DB:-binfin}"
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /docker-entrypoint-initdb.d/init.sql

echo "Downloading Ollama model..."
docker compose up -d ollama
sleep 5
docker compose exec -T ollama ollama pull mistral:7b-instruct-q4_K_M

echo "Downloading historical data (this may take a while)..."
python scripts/download_historical_data.py || true

echo "Training initial ML models..."
python scripts/train_models.py --coins BTC ETH DOGE || true

echo "Starting all services..."
docker compose up -d

echo "Running tests..."
pytest tests/ -v

echo "Setup complete"
echo ""
echo "Access points:"
echo "  - API: http://localhost:8000"
echo "  - API Docs: http://localhost:8000/docs"
echo "  - Web Dashboard: http://localhost:8501"
echo "  - Prometheus: http://localhost:9090"
echo "  - Grafana: http://localhost:3000"
echo ""
echo "To start CLI dashboard: python cli/terminal.py"
echo "To view logs: docker compose logs -f"
