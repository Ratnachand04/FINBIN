# LLM Fine-Tuning API Integration Guide

This guide covers using the REST API (instead of shell scripts) to trigger and monitor Mistral 7B fine-tuning jobs from the dashboard or backend.

## Quick Start

### 1. Ensure All Services Are Running

```bash
# Deploy with Celery worker enabled
./scripts/deploy_model.ps1 -FineTune   # Windows
./scripts/deploy_model.sh --fine-tune   # Linux/macOS
```

This starts:
- PostgreSQL (database)
- Redis (task broker)
- Ollama (inference engine)
- Backend API (FastAPI on port 8000)
- Celery Worker (async job executor)
- LLM Trainer (on-demand via Docker Compose profile)

### 2. Trigger a Fine-Tuning Job via API

**Option A: Using cURL**
```bash
curl -X POST http://localhost:8000/api/v1/llm/finetune \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_name": "my-custom-v1",
    "ollama_model_name": "my-finance-bot",
    "dataset_limit": 5000,
    "epochs": 1,
    "learning_rate": 0.0002
  }' | jq .job_id
```

**Option B: Using Python**
```python
import requests

response = requests.post(
    "http://localhost:8000/api/v1/llm/finetune",
    json={
        "adapter_name": "my-custom-v1",
        "ollama_model_name": "my-finance-bot",
        "dataset_limit": 5000,
        "epochs": 1,
    }
)
job_id = response.json()["job_id"]
print(f"Job started: {job_id}")
```

### 3. Monitor Job Status

```python
import time
import requests

job_id = "a1b2c3d4e5f6g7h8"  # from start response

while True:
    status = requests.get(f"http://localhost:8000/api/v1/llm/finetune/{job_id}").json()
    print(f"Status: {status['status']}, Progress: {status['progress_percent']}%")
    
    if status["status"] in ["completed", "failed"]:
        break
    time.sleep(5)

if status["status"] == "completed":
    print(f"✅ Model ready: {status['ollama_model_name']}")
else:
    print(f"❌ Error: {status['error_message']}")
```

---

## API Reference

### Start Fine-Tuning Job

```http
POST /api/v1/llm/finetune
Content-Type: application/json

{
  "adapter_name": "my-adapter",
  "ollama_model_name": "my-model",
  "dataset_limit": 15000,
  "epochs": 1,
  "learning_rate": 0.0002,
  "batch_size": 1,
  "grad_accum": 16
}
```

**Response (202):**
```json
{
  "job_id": "ab12cd34ef56gh78",
  "status": "pending",
  "adapter_name": "my-adapter",
  "ollama_model_name": "my-model",
  "progress_percent": 0,
  "created_at": "2025-01-15T10:30:00+00:00"
}
```

### Check Job Status

```http
GET /api/v1/llm/finetune/{job_id}
```

**Response (200):**
```json
{
  "job_id": "ab12cd34ef56gh78",
  "status": "running",
  "adapter_name": "my-adapter",
  "ollama_model_name": "my-model",
  "progress_percent": 45,
  "dataset_limit": 15000,
  "epochs": 1,
  "learning_rate": 0.0002,
  "batch_size": 1,
  "grad_accum": 16,
  "created_at": "2025-01-15T10:30:00+00:00",
  "started_at": "2025-01-15T10:31:00+00:00",
  "completed_at": null,
  "error_message": null,
  "updated_at": "2025-01-15T11:15:00+00:00"
}
```

**Status Values:**
- `pending` - Queued, waiting to start
- `running` - Currently training
- `completed` - Successfully finished
- `failed` - Encountered error
- `cancelled` - Manually stopped

### List All Jobs

```http
GET /api/v1/llm/finetune?status=completed&limit=10
```

**Response (200):**
```json
[
  {
    "job_id": "ab12cd34ef56gh78",
    "status": "completed",
    "adapter_name": "my-adapter",
    "ollama_model_name": "my-model",
    "progress_percent": 100,
    "created_at": "2025-01-15T10:30:00+00:00",
    "started_at": "2025-01-15T10:31:00+00:00",
    "completed_at": "2025-01-15T12:45:00+00:00",
    "error_message": null
  }
]
```

### Cancel Job

```http
GET /api/v1/llm/finetune/{job_id}/cancel
```

**Response (200):**
```json
{
  "status": "cancelled",
  "job_id": "ab12cd34ef56gh78"
}
```

---

## Architecture

```
┌─────────────────────────────────────────┐
│ Dashboard / External Client             │
└──────────────────┬──────────────────────┘
                   │ HTTP
                   ▼
        ┌─────────────────────┐
        │  FastAPI Backend    │◄─── /api/v1/llm/finetune
        │  (port 8000)        │
        └──────────┬──────────┘
                   │ enqueue task
                   ▼
        ┌─────────────────────┐
        │  Redis Queue        │
        │  (broker)           │
        └──────────┬──────────┘
                   │ consume
                   ▼
        ┌─────────────────────┐
        │ Celery Worker       │◄─── runs tasks
        │ (concurrency=1)     │
        └──────────┬──────────┘
                   │ execute
                   ▼
        ┌─────────────────────┐
        │ Docker Compose      │
        │ - PostgreSQL        │
        │ - Ollama            │
        │ - LLM Trainer       │
        └─────────────────────┘
```

### Async Execution Flow

1. **Client Submission**
   - POST request to `/api/v1/llm/finetune`
   - Request validated via Pydantic models
   - Job record created in PostgreSQL (status="pending")

2. **Task Queuing**
   - Celery task `finetune.run_qlora` enqueued to Redis
   - Returns job_id immediately for polling

3. **Worker Pickup**
   - Celery worker polls Redis for tasks
   - Picks up `run_qlora` task (FIFO, max 1 concurrent)
   - Updates DB: status="running"

4. **Training Execution**
   - Shell script (`run_llm_finetune.sh` or `.ps1`) invoked
   - Steps:
     - Start PostgreSQL + Ollama containers
     - Export SFT dataset from news_articles table
     - Run QLoRA training in llm-trainer container
     - Generate Ollama Modelfile
     - Copy adapter to Ollama container
     - `ollama create <model_name>` registers new model

5. **Completion**
   - If successful: status="completed", progress=100%
   - If failed: status="failed", error_message populated
   - Model available for inference via `/llm/inference` endpoints

---

## Performance Considerations

### Dataset Limit
- **Small**: 1000-5000 samples → ~10-30 min training
- **Medium**: 5000-15000 samples → ~30-90 min training  
- **Large**: 15000-100000 samples → 2-8 hours training

### GPU Requirements
- **Minimum**: NVIDIA RTX 2060 (6GB VRAM) with QLoRA quantization
- **Recommended**: RTX 3090 / 4090 for faster convergence
- **CPU-only**: Technically supported but impractical (>24 hours per epoch)

### Cost-Benefit
| Parameter | Duration Impact | Quality Impact |
|-----------|-----------------|----------------|
| ↑ dataset_limit | +30% per 5k samples | ✅ Better generalization |
| ↑ epochs | +100% per epoch | ✅ Deeper learning (diminishing) |
| ↓ learning_rate | +20% convergence time | ✅ More stable |
| ↑ batch_size | -10% per doubling | ✅ More stable, but VRAM intensive |
| ↑ grad_accum | -5% per doubling | ✅ Enhanced stability |

---

## Troubleshooting

### "Job stuck in pending"
- Check Celery worker is running: `docker ps | grep celery-worker`
- Check Redis connection: `docker compose exec redis redis-cli ping`
- Celery worker logs: `docker compose logs celery-worker`

### "Redis connection refused"
- Ensure Redis is running: `docker compose up -d redis`
- Check CELERY_BROKER_URL env var points to correct Redis

### "Script not found: run_llm_finetune.ps1"
- Verify `scripts/run_llm_finetune.ps1` exists
- Check PowerShell execution policy: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### "GPU out of memory"
- Reduce `batch_size` (try 1)
- Reduce `dataset_limit` (try 5000)
- Reduce `grad_accum` (try 4)

### "Job cancelled unexpectedly"
- Check worker logs: `docker compose logs celery-worker`
- Verify task time limits not hit (default 24 hours)
- Check PostgreSQL/Redis availability during runtime

---

## Integration with Dashboard

### Example: Streamlit Component

```python
# frontend/components/finetune.py
import streamlit as st
import requests
import time

BASE_URL = "http://backend:8000"

st.subheader("🤖 Mistral Fine-Tuning")

with st.form("finetune_form"):
    adapter_name = st.text_input("Adapter Name", value="custom-v1")
    model_name = st.text_input("Ollama Model Name", value="finance-model")
    dataset_limit = st.slider("Dataset Size", 100, 100000, 15000, step=1000)
    epochs = st.slider("Epochs", 0.1, 10.0, 1.0, step=0.1)
    lr = st.select_slider("Learning Rate", options=[1e-5, 1e-4, 3e-4, 1e-3], value=2e-4)
    
    submitted = st.form_submit_button("🚀 Start Fine-Tuning")
    
    if submitted:
        response = requests.post(
            f"{BASE_URL}/api/v1/llm/finetune",
            json={
                "adapter_name": adapter_name,
                "ollama_model_name": model_name,
                "dataset_limit": dataset_limit,
                "epochs": epochs,
                "learning_rate": lr,
            }
        )
        
        if response.status_code == 200:
            job = response.json()
            st.success(f"✅ Job started: {job['job_id']}")
            st.session_state.job_id = job['job_id']
            st.session_state.start_time = time.time()

# Status monitor
if hasattr(st.session_state, 'job_id'):
    job_id = st.session_state.job_id
    status = requests.get(f"{BASE_URL}/api/v1/llm/finetune/{job_id}").json()
    
    col1, col2 = st.columns(2)
    col1.metric("Status", status['status'].upper())
    col2.metric("Progress", f"{status['progress_percent']}%")
    
    if status['status'] == "running":
        st.progress(status['progress_percent'] / 100.0)
    elif status['status'] == "completed":
        st.success(f"✅ Model ready: **{status['ollama_model_name']}**")
    elif status['status'] == "failed":
        st.error(f"❌ Error: {status['error_message']}")
```

---

## Deployment Checklist

- [ ] PostgreSQL running with `finetune_jobs` table (auto-created)
- [ ] Redis running on port 6379 with databases 1 & 2 available
- [ ] Backend container built and running on port 8000
- [ ] Celery worker container running with proper Redis env vars
- [ ] LLM trainer Dockerfile present at `llm_trainer/Dockerfile`
- [ ] Scripts `run_llm_finetune.ps1` and `run_llm_finetune.sh` present
- [ ] Environment variables set: `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`
- [ ] Firewall allows container inter-communication on binfin_net bridge

---

## Advanced Usage

### Monitoring All Jobs
```bash
curl 'http://localhost:8000/api/v1/llm/finetune?limit=100' | jq '.[] | {job_id, status, progress_percent}'
```

### Filtering by Status
```bash
curl 'http://localhost:8000/api/v1/llm/finetune?status=failed' | jq .
```

### Check Celery Worker Health
```bash
docker compose exec celery-worker celery -A backend.workers.finetune_worker inspect active
```

### View Celery Queue
```bash
docker compose exec redis redis-cli LLEN celery
```

### Manual Celery Task Revocation (Force Stop)
```python
from backend.workers.finetune_worker import run_qlora_finetune
run_qlora_finetune.revoke(task_id="<job_id>", terminate=True)
```

---

## Full API Documentation

Interactive Swagger UI available at:
- **Local**: `http://localhost:8000/docs`
- **Filter**: Search for `/llm/finetune` in the Swagger sidebar

All endpoints require:
- `Content-Type: application/json` (for POST)
- Valid request body schema (validated by Pydantic)

See [API_FINETUNE.md](API_FINETUNE.md) for detailed endpoint specs, error codes, and examples.
