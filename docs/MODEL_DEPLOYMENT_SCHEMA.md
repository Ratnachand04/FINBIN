# BINFIN Model Deployment Schema

## Goal
Run the complete BINFIN model stack with a single command.

The deployment must:
- Pull and run all frontend containers.
- Pull and run all backend and worker containers.
- Pull and run all local LLM containers (Ollama + Mistral 7B).
- Prefer GPU for local LLM execution and automatically fall back to CPU if GPU startup fails.

## Single-Command Deployment

Windows:

```powershell
./scripts/deploy_model.ps1
```

Linux/macOS:

```bash
./scripts/deploy_model.sh
```

## Runtime Flow

1. Ensure `.env` exists (auto-created from `.env.example` if missing).
2. Pull all images from `docker-compose.yml`.
3. Attempt GPU startup using compose override `docker-compose.gpu.yml`.
4. If GPU startup fails, shut down failed stack and start CPU-safe stack from `docker-compose.yml`.
5. Wait for backend and frontend health endpoints.
6. Ensure Ollama model is present:
   - `mistral:7b-instruct-q4_K_M`

## Containers Covered

Frontend:
- `frontend`

Backend and workers:
- `backend`
- `data-ingestion-worker`
- `ml-processing-worker`
- `signal-generator-worker`

Local LLM:
- `ollama`

Core infra:
- `postgres`
- `redis`
- `prometheus`
- `grafana`

## Data Training Flow

Optional one-command deploy + training:

Windows:

```powershell
./scripts/deploy_model.ps1 -Train
```

Linux/macOS:

```bash
./scripts/deploy_model.sh --train
```

Optional one-command deploy + true QLoRA fine-tuning + Ollama packaging:

Windows:

```powershell
./scripts/deploy_model.ps1 -FineTune
```

Linux/macOS:

```bash
./scripts/deploy_model.sh --fine-tune
```

The training call uses:
- `POST /api/v1/model/train-finance-news`

This endpoint trains prediction workflow with finance-news sentiment, using data already in DB:
- `price_data`
- `news_articles`

## API Keys and Data Ingestion

To train with external data, set API keys in `.env`:
- `NEWS_API_KEY`
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- optional Binance keys

Ingestion workers use these keys to keep pulling new market/news data.

## GPU Fallback Policy

- GPU path: `docker-compose.yml + docker-compose.gpu.yml`
- CPU path: `docker-compose.yml` only
- If GPU path fails to start, deployment automatically switches to CPU path.

This guarantees local LLM availability even on systems without stable GPU runtime.
