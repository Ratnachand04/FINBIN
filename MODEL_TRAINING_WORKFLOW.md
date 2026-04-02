# BINFIN Complete Model Training and Hot-Swap Workflow

This guide covers the end-to-end process of training the self-hosted Large Language Model (LLM) using your own data, optimizing it via QLoRA, and seamlessly replacing the live model in the running deployment.

## 1. Preparing Your Data
The `llm_trainer` container within the BINFIN stack is pre-configured to handle `.jsonl` and `.json` datasets. 
To train the model on personalized data (e.g., custom financial news, proprietary trading strategies, or specific market sentiment inputs):

1. **Format your data** into a JSON Lines format where each line contains a prompt and completion or a standard conversational dataset. E.g.:
	```json
	{"text": "[INST] Analyze the sentiment: AMZN earnings beat expectations. [/INST] Bullish."}
	{"text": "[INST] Provide technical analysis for BTC crossing 200 SMA. [/INST] This signals a long-term bullish reversal..."}
	```
2. **Place the dataset** in the `llm_trainer/data/` directory (mapped to `/workspace/artifacts/data/` inside the container).

## 2. Initiating the Training
The single Docker deployment includes a profile specifically for training. Once your data is in place, you can kick off the QLoRA optimization:

If you want to run it dynamically as a standalone container:
```bash
docker compose --profile llm-train up binfin-llm-trainer
```

**What happens?**
- The container executes `train_qlora.py`.
- It loads the base model (e.g., `mistralai/Mistral-7B-Instruct-v0.3`).
- It applies **QLoRA** (Quantized Low-Rank Adaptation) to efficiently train the adapter weights using minimal VRAM.
- Once finished, it saves the optimized adapter to `/workspace/artifacts/output/adapters/binfin-mistral-qlora`.

## 3. Hot-Swapping the Production Model
The unique proposition of BINFIN is that the custom model can be immediately plugged into the live system without tearing down the infrastructure. 

Run the automated replacement script included in the `scripts/` directory:
```bash
docker exec -it binfin-celery-worker bash /workspace/scripts/replace_model.sh
```

**Behind the Scenes:**
1. **Weight Merging**: The script loads the base model weights and merges your freshly trained PEFT adapter into a cohesive set.
2. **Quantization & Format Conversion**: It utilizes `llama.cpp` to instantly convert the merged PyTorch weights into the widely-supported `.gguf` format (`q4_k_m` optimized for speed/quality).
3. **Ollama Integration**: It constructs a custom `Modelfile` embedding your system prompts, then pipes the `.gguf` directly into the active `binfin-ollama` container via API.
4. **Live Switch**: The model, now named `binfin-custom`, is instantly available for queries via the BINFIN REST backend and Streamlit dashboard.

## 4. Automatic Live Data Loop
Within the **BINFIN Frontend Dashboard**, users can now navigate to the **API Keys** tab. By saving their API Keys for sources like Binance, NewsAPI, and HuggingFace, the backend workers fetch live feeds tailored to the user's accounts. 

You can funnel this live parsed data into the training data loop regularly to keep `binfin-custom` constantly updated and optimized against shifting market regimes.
