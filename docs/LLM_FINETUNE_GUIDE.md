# BINFIN True LLM Fine-Tuning Guide (LoRA/QLoRA)

## What this adds

This repository now includes true adapter fine-tuning for Mistral 7B inside the Docker stack.

Components:
- `llm-trainer` service in `docker-compose.yml` (profile: `llm-train`)
- Dataset export script: `llm_trainer/export_dataset.py`
- QLoRA training script: `llm_trainer/train_qlora.py`
- Ollama packaging script: `llm_trainer/package_ollama.py`
- End-to-end runners:
  - `scripts/run_llm_finetune.ps1`
  - `scripts/run_llm_finetune.sh`

## End-to-end command

Windows:

```powershell
./scripts/run_llm_finetune.ps1
```

Linux/macOS:

```bash
./scripts/run_llm_finetune.sh
```

## What the command does

1. Starts required services (`postgres`, `ollama`).
2. Exports supervised SFT dataset from `news_articles` into JSONL.
3. Runs QLoRA on `mistralai/Mistral-7B-Instruct-v0.3` with PEFT/TRL.
4. Saves adapter weights under `llm_trainer/artifacts/output/adapters/`.
5. Generates Ollama `Modelfile` using `ADAPTER` directive.
6. Copies adapter + Modelfile into `ollama` container.
7. Builds final local model with `ollama create`.

## Outputs

- Dataset: `llm_trainer/artifacts/data/finance_news_sft.jsonl`
- Adapter: `llm_trainer/artifacts/output/adapters/binfin-mistral-qlora`
- Modelfile: `llm_trainer/artifacts/ollama/Modelfile`
- Ollama model (default): `binfin-mistral-finance`

## Customize training

PowerShell example:

```powershell
./scripts/run_llm_finetune.ps1 -DatasetLimit 25000 -Epochs 2 -LearningRate 0.00015 -BatchSize 1 -GradAccum 24 -AdapterName binfin-qlora-v2 -OllamaModelName binfin-mistral-finance-v2
```

Bash example:

```bash
./scripts/run_llm_finetune.sh --dataset-limit 25000 --epochs 2 --lr 0.00015 --batch-size 1 --grad-accum 24 --adapter-name binfin-qlora-v2 --ollama-model-name binfin-mistral-finance-v2
```

## Notes

- QLoRA needs a CUDA-capable GPU for practical training speed.
- For inference fallback, your deployment scripts already support GPU-first and CPU fallback at service start.
- Training uses a heuristic-supervised dataset generated from your news DB. You can replace this with curated labels for higher quality.
