#!/bin/bash
set -e

# This script merges the PEFT adapter into the Mistral Base Model and compiles to GGUF.
# It requires llama.cpp repository cloned locally and requirements installed.

echo "[1/2] Merging Base Mistral with Adapter Weights locally..."
cat << 'EOF' > merge.py
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

# Local paths to the downloaded model and newly generated adapter
base_model_name = "mistralai/Mistral-7B-v0.1"
adapter_path = "./output_adapter"
merged_dir = "./output_merged"

base_model = AutoModelForCausalLM.from_pretrained(base_model_name, torch_dtype=torch.float16, device_map="cpu")
model = PeftModel.from_pretrained(base_model, adapter_path)
model = model.merge_and_unload()
model.save_pretrained(merged_dir, safe_serialization=True)

tokenizer = AutoTokenizer.from_pretrained(base_model_name)
tokenizer.save_pretrained(merged_dir)
EOF

python3 merge.py

echo "[2/2] Compiling to GGUF format..."
if [ ! -d "llama.cpp" ]; then
    git clone https://github.com/ggerganov/llama.cpp.git
    pip install -r llama.cpp/requirements.txt
fi

python3 llama.cpp/convert_hf_to_gguf.py ./output_merged \
    --outfile ../docker/ollama/binfin-mistral.gguf \
    --outtype q4_k_m

echo "Success! Custom model successfully baked to ../docker/ollama/binfin-mistral.gguf"
echo "You can now run 'start.bat' or 'docker compose up --build -d' to deploy."
