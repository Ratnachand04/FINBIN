# Fine-Tuning API Endpoints

The fine-tuning API allows you to trigger and monitor QLoRA fine-tuning jobs for Mistral 7B from the dashboard or backend API.

## Endpoints

### Start Fine-Tuning Job

**POST** `/api/v1/llm/finetune`

Trigger a new asynchronous fine-tuning job.

**Request Body:**
```json
{
  "adapter_name": "binfin-mistral-custom-v1",
  "ollama_model_name": "my-finance-model",
  "dataset_limit": 15000,
  "epochs": 1,
  "learning_rate": 0.0002,
  "batch_size": 1,
  "grad_accum": 16
}
```

**Response (202 Accepted):**
```json
{
  "job_id": "a1b2c3d4e5f6g7h8",
  "status": "pending",
  "adapter_name": "binfin-mistral-custom-v1",
  "ollama_model_name": "my-finance-model",
  "progress_percent": 0,
  "created_at": "2025-01-15T10:30:00+00:00"
}
```

**Parameters:**
- `adapter_name` (string, optional): Identifier for LoRA adapter weights. Default: `binfin-mistral-qlora`
- `ollama_model_name` (string, optional): Name to register in Ollama. Default: `binfin-mistral-finance`
- `dataset_limit` (integer, optional): Max training examples from database. Range: 100-100000. Default: 15000
- `epochs` (number, optional): Training epochs. Range: 0.1-10. Default: 1.0
- `learning_rate` (number, optional): QLoRA learning rate. Range: 1e-6 to 0.01. Default: 0.0002
- `batch_size` (integer, optional): Per-GPU batch size. Range: 1-4. Default: 1
- `grad_accum` (integer, optional): Gradient accumulation steps. Range: 1-128. Default: 16

---

### Get Job Status

**GET** `/api/v1/llm/finetune/{job_id}`

Retrieve detailed status and progress for a specific fine-tuning job.

**Response (200 OK):**
```json
{
  "job_id": "a1b2c3d4e5f6g7h8",
  "status": "running",
  "adapter_name": "binfin-mistral-custom-v1",
  "ollama_model_name": "my-finance-model",
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

**Possible Status Values:**
- `pending`: Queued, waiting to start
- `running`: Currently training
- `completed`: Successfully finished
- `failed`: Encountered an error
- `cancelled`: Manually cancelled by user

---

### List Fine-Tuning Jobs

**GET** `/api/v1/llm/finetune`

List all fine-tuning jobs with optional filtering.

**Query Parameters:**
- `status` (string, optional): Filter by status (`pending|running|completed|failed`)
- `limit` (integer, optional): Number of results. Default: 50. Max: 500
- `offset` (integer, optional): Pagination offset. Default: 0

**Response (200 OK):**
```json
[
  {
    "job_id": "a1b2c3d4e5f6g7h8",
    "status": "completed",
    "adapter_name": "binfin-mistral-custom-v1",
    "ollama_model_name": "my-finance-model",
    "progress_percent": 100,
    "created_at": "2025-01-15T10:30:00+00:00",
    "started_at": "2025-01-15T10:31:00+00:00",
    "completed_at": "2025-01-15T12:45:00+00:00",
    "error_message": null
  },
  {
    "job_id": "x9y8z7w6v5u4t3s2",
    "status": "running",
    "adapter_name": "binfin-mistral-qlora",
    "ollama_model_name": "binfin-mistral-finance",
    "progress_percent": 60,
    "created_at": "2025-01-15T11:00:00+00:00",
    "started_at": "2025-01-15T11:01:00+00:00",
    "completed_at": null,
    "error_message": null
  }
]
```

---

### Cancel Job

**GET** `/api/v1/llm/finetune/{job_id}/cancel`

Cancel a pending or running fine-tuning job.

**Response (200 OK):**
```json
{
  "status": "cancelled",
  "job_id": "a1b2c3d4e5f6g7h8"
}
```

**Error Cases:**
- `404`: Job not found
- `400`: Cannot cancel job in status `completed`, `failed`, or `cancelled`

---

## Usage Examples

### Python with requests

```python
import requests
import time

BASE_URL = "http://localhost:8000"

# Start a fine-tuning job
response = requests.post(
    f"{BASE_URL}/api/v1/llm/finetune",
    json={
        "adapter_name": "my-finance-v2",
        "ollama_model_name": "finance-bot",
        "epochs": 2,
        "learning_rate": 0.0001,
    }
)
job = response.json()
job_id = job["job_id"]
print(f"Started job: {job_id}")

# Poll for completion
while True:
    status_response = requests.get(f"{BASE_URL}/api/v1/llm/finetune/{job_id}")
    status = status_response.json()
    print(f"Status: {status['status']}, Progress: {status['progress_percent']}%")
    
    if status["status"] in ["completed", "failed"]:
        break
    
    time.sleep(10)

if status["status"] == "completed":
    print(f"Model ready: {status['ollama_model_name']}")
else:
    print(f"Job failed: {status['error_message']}")
```

### cURL

```bash
# Start job
curl -X POST http://localhost:8000/api/v1/llm/finetune \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_name": "my-custom-adapter",
    "ollama_model_name": "my-model",
    "dataset_limit": 5000,
    "epochs": 1,
    "learning_rate": 0.0002
  }' | jq .

# Check status (replace JOB_ID)
curl http://localhost:8000/api/v1/llm/finetune/JOB_ID | jq .

# List all jobs
curl 'http://localhost:8000/api/v1/llm/finetune?status=completed' | jq .

# Cancel job
curl 'http://localhost:8000/api/v1/llm/finetune/JOB_ID/cancel' | jq .
```

### JavaScript/Node.js

```javascript
const BASE_URL = "http://localhost:8000";

async function startFineTuning() {
  const response = await fetch(`${BASE_URL}/api/v1/llm/finetune`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      adapter_name: "web-ui-adapter",
      ollama_model_name: "web-fine-tuned",
      epochs: 1
    })
  });
  
  const job = await response.json();
  return job.job_id;
}

async function checkStatus(jobId) {
  const response = await fetch(`${BASE_URL}/api/v1/llm/finetune/${jobId}`);
  return await response.json();
}

async function waitForCompletion(jobId) {
  while (true) {
    const status = await checkStatus(jobId);
    console.log(`Status: ${status.status} (${status.progress_percent}%)`);
    
    if (["completed", "failed"].includes(status.status)) {
      return status;
    }
    
    await new Promise(r => setTimeout(r, 5000));
  }
}

// Usage
const jobId = await startFineTuning();
const finalStatus = await waitForCompletion(jobId);
console.log(finalStatus);
```

---

## Configuration Parameters Explained

### `learning_rate`
Controls how quickly the model adapts to news data. Lower values = slower, more stable learning; higher values = faster but riskier.
- **Default: 0.0002** (recommended for stable convergence)
- **Typical range: 1e-5 to 1e-3**

### `epochs`
Number of complete passes through the training dataset.
- **1.0 epoch**: All samples used once (usual for large datasets like 15k samples)
- **2.0+ epochs**: Repeated training (useful for small datasets <1000 samples)

### `batch_size`
Samples processed per GPU forward pass. Higher values = faster but more memory.
- **1-2**: Safe for most GPUs, slower iteration
- **4+**: Requires higher VRAM; may improve speed on RTX 4090+

### `grad_accum`
Accumulates gradients across steps before updating weights (simulates larger effective batch size without more VRAM).
- **Default: 16** (effective batch = batch_size × grad_accum = 16 samples)
- *Higher values* = more stable gradients but fewer weight updates per epoch

### `dataset_limit`
Maximum training examples extracted from `news_articles` table.
- **Recommended: 5000-15000** for stable convergence
- **Max: 100000** (all available if sufficient)

---

## Job Lifecycle

```
pending → running → completed (success case)
       ↘           ↙
        failed (error case)
         
running → cancelled (if manually stopped)
```

During `running`:
- Model trains on GPU (if available)
- Adapter weights updated incrementally
- Progress tracked in database

After `completed`:
- Adapter exported to `llm_trainer/artifacts/output/adapters/{adapter_name}/`
- Modelfile generated
- Ollama container notified to load new adapter
- Model available for inference via Ollama at port 11434

---

## Backend Architecture

### Async Task Queue (Celery)
- **Broker**: Redis (port 6379, db 1)
- **Backend**: Redis (port 6379, db 2)
- **Task Name**: `finetune.run_qlora`

### Database Tracking
- **Table**: `finetune_jobs` (PostgreSQL)
- **Columns**: job_id, status, adapter_name, ollama_model_name, progress_percent, error_message, created_at, updated_at, started_at, completed_at
- **Indexes**: job_id (unique), status (for filtering)

### Execution Flow
1. API receives POST request → create DB record with status="pending"
2. Celery task queued → task polls status → updated to "running"
3. Shell script invoked (docker compose profile + trainer container)
4. Dataset exported → training begins → Ollama model packaged
5. On completion: status="completed", progress=100%
6. On error: status="failed", error_message set

---

## Error Handling

| Error | Cause | Resolution |
|-------|-------|-----------|
| `script not found` | run_llm_finetune.ps1/sh missing | Ensure scripts/ directory exists |
| `Redis connection refused` | Redis not running | `docker compose up -d redis` |
| `PostgreSQL connection failed` | Database unreachable | Check DATABASE_URL env var |
| `GPU out of memory` | Batch size too large | Reduce batch_size or grad_accum |
| `timeout after 24 hours` | Training too slow | Reduce dataset_limit or epochs |

---

## OpenAPI Documentation

Full interactive API docs available at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

Filter for `/api/v1/llm/finetune*` endpoints in the schema.
