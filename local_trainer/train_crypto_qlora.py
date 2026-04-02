"""
Local QLoRA Fine-tuning Script designed to run strictly on the host CUDA machine
before `docker compose up --build`.

It expects a JSONL dataset with pairs of instructions and responses mapping crypto 
news headlines to price movement actions.
"""

import os
import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainingArguments
from trl import SFTTrainer

# Base Mistral model mapped for quant
MODEL_NAME = "mistralai/Mistral-7B-v0.1"
OUTPUT_DIR = "./output_adapter"
DATASET_PATH = "finance_crypto_sft.jsonl"

def main():
    if not os.path.exists(DATASET_PATH):
        print(f"Dataset {DATASET_PATH} not found. Please create one with your domain specific data before running.")
        return

    print("Loading Mistral in 4-bit...")
    quantization_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
    )

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME, 
        quantization_config=quantization_config, 
        device_map="auto"
    )

    print("Configuring QLoRA...")
    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "v_proj"],
        bias="none",
        task_type="CAUSAL_LM"
    )

    dataset = load_dataset("json", data_files=DATASET_PATH, split="train")

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        gradient_checkpointing=True,
        optim="paged_adamw_32bit",
        save_steps=100,
        logging_steps=1,
        learning_rate=2e-4,
        fp16=True,
        max_grad_norm=0.3,
        num_train_epochs=1,    # Full dataset pass. Utilizing minimal params (q_proj, v_proj) for optimization.
        warmup_ratio=0.03,
        group_by_length=True,
        lr_scheduler_type="cosine",
    )

    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset,
        peft_config=peft_config,
        dataset_text_field="text",
        max_seq_length=512,
        tokenizer=tokenizer,
        args=training_args,
    )

    print("Beginning Training...")
    trainer.train()

    print(f"Saving PEFT adapter to {OUTPUT_DIR}")
    trainer.model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

if __name__ == "__main__":
    main()
