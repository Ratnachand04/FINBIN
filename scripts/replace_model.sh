#!/bin/bash
set -e

# BINFIN Model Replacement Script
# This script converts a fine-tuned LoRA adapter into an Ollama-compatible format and hot-swaps the model.
# NOTE: This requires `llama.cpp` tools to be installed or available in the path to convert to GGUF.

ADAPTER_DIR="/workspace/artifacts/output/adapters/binfin-mistral-qlora"
BASE_MODEL="mistralai/Mistral-7B-Instruct-v0.3"
MERGED_DIR="/workspace/artifacts/output/merged"
GGUF_OUTPUT="/workspace/artifacts/output/binfin-custom-expert.gguf"
OLLAMA_MODEL_NAME="binfin-custom"

echo "=========================================================="
echo " Starting BINFIN Model Hot-Swap Process"
echo "=========================================================="

# 1. Merge Adapter with Base Model
echo "[1/4] Merging LoRA adapter with base model..."
# Note: You need a short python script to load the base model, load the PEFT adapter, and merge_and_unload().
cat << 'EOF' > /workspace/artifacts/output/merge.py
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

base_model_name = "mistralai/Mistral-7B-Instruct-v0.3"
adapter_path = "/workspace/artifacts/output/adapters/binfin-mistral-qlora"
merged_dir = "/workspace/artifacts/output/merged"

print("Loading base model...")
base_model = AutoModelForCausalLM.from_pretrained(
    base_model_name,
    torch_dtype=torch.float16,
    device_map="cpu",
)
print("Loading adapter...")
model = PeftModel.from_pretrained(base_model, adapter_path)

print("Merging weights...")
model = model.merge_and_unload()

print("Saving merged model...")
model.save_pretrained(merged_dir, safe_serialization=True)
tokenizer = AutoTokenizer.from_pretrained(base_model_name)
tokenizer.save_pretrained(merged_dir)
print("Merge complete.")
EOF

python3 /workspace/artifacts/output/merge.py

# 2. Convert to GGUF
echo "[2/4] Converting to GGUF format..."
if [ ! -d "/workspace/llama.cpp" ]; then
    echo "Cloning llama.cpp to perform conversion..."
    git clone https://github.com/ggerganov/llama.cpp.git /workspace/llama.cpp
    pip install -r /workspace/llama.cpp/requirements.txt
fi

python3 /workspace/llama.cpp/convert_hf_to_gguf.py $MERGED_DIR \
    --outfile $GGUF_OUTPUT \
    --outtype q4_k_m

# 3. Create Ollama Modelfile
echo "[3/4] Creating Ollama Modelfile..."
cat << EOF > /workspace/artifacts/output/Modelfile
FROM $GGUF_OUTPUT

TEMPLATE """[INST] {{ .System }}

{{ .Prompt }} [/INST]"""

SYSTEM """You are BINFIN, a specialized AI trained to analyze financial data, sentiment, and provide market insights."""

PARAMETER stop "[INST]"
PARAMETER stop "[/INST]"
EOF

# 4. Push to Ollama
echo "[4/4] Importing model into Ollama..."
# We assume the ollama container is running and accessible at localhost:11434.
# If running inside a docker container, ensure the bridge network allows calls to `binfin-ollama:11434`.
OLLAMA_HOST="http://binfin-ollama:11434" ollama create $OLLAMA_MODEL_NAME -f /workspace/artifacts/output/Modelfile

echo "=========================================================="
echo " Success! Custom model '$OLLAMA_MODEL_NAME' is active."
echo "=========================================================="
