# CUDA Integration Plan for Sentiment + Prediction Pipeline

## Objective
Use GPU acceleration (CUDA) for local model inference when available, and automatically fall back to CPU mode on a 16 GB RAM laptop when CUDA is not available.

## Current Runtime Policy
- Prefer CUDA when `torch.cuda.is_available()` is true.
- Use CPU by default when CUDA is unavailable.
- Keep the Ollama model on quantized Mistral 7B (`mistral:7b-instruct-q4_K_M`) for lower memory footprint.

## Implementation Steps
1. Driver and toolkit readiness.
- Install latest NVIDIA drivers.
- Verify GPU is visible with `nvidia-smi`.

2. Python environment validation.
- Install CUDA-enabled PyTorch build where applicable.
- Validate with:
  - `python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"`

3. Enable GPU path for FinBERT sentiment fallback.
- Set environment variable `ENABLE_GPU=true` when CUDA is available.
- Keep `ENABLE_GPU=false` for CPU-only machines.

4. Ollama runtime optimization.
- Use quantized Mistral 7B model.
- Reduce concurrent request batch size if VRAM pressure appears.

5. Safe fallback defaults.
- If CUDA checks fail at runtime, use CPU mode.
- Cap training rows per symbol in UI to control memory and latency.

## Recommended Default Settings
- GPU-capable laptop/desktop:
  - `ENABLE_GPU=true`
  - `OLLAMA_MODEL=mistral:7b-instruct-q4_K_M`
  - Retraining rows per symbol: 6000 to 12000

- CPU-only 16 GB RAM laptop:
  - `ENABLE_GPU=false`
  - `OLLAMA_MODEL=mistral:7b-instruct-q4_K_M`
  - Retraining rows per symbol: 3000 to 6000

## Operational Verification Checklist
- Backend endpoint `GET /api/v1/model/runtime` reports:
  - `cuda_available`
  - `selected_device`
  - `ollama_reachable`
- Streamlit Model tab shows runtime metrics and can complete retraining for BTC.
- New rows are written to:
  - `sentiment_scores`
  - `price_predictions`

## Rollback Plan
If inference becomes unstable or memory usage spikes:
- Set `ENABLE_GPU=false`.
- Lower retraining rows per symbol in Streamlit.
- Keep sentiment-only flow active and pause retraining endpoint usage.
