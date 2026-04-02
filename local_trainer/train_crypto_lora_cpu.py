"""
Local CPU LoRA fine-tuning script for Mistral.

This path does not use bitsandbytes or 4-bit quantization. It is intended for
CPU-only hosts where training speed is lower but hardware requirements are simple.
"""

from __future__ import annotations

import os

import torch
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from trl import SFTTrainer

MODEL_NAME = "mistralai/Mistral-7B-v0.1"
OUTPUT_DIR = "./output_adapter_cpu"
DATASET_PATH = "finance_crypto_sft.jsonl"


def main() -> None:
    if not os.path.exists(DATASET_PATH):
        print(f"Dataset {DATASET_PATH} not found. Please create one before running.")
        return

    print("Loading Mistral on CPU for standard LoRA...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        device_map="cpu",
        torch_dtype=torch.float32,
        low_cpu_mem_usage=True,
    )
    model.config.use_cache = False

    print("Configuring CPU LoRA...")
    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        target_modules=["q_proj", "v_proj"],
        bias="none",
        task_type="CAUSAL_LM",
    )

    dataset = load_dataset("json", data_files=DATASET_PATH, split="train")

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        gradient_checkpointing=True,
        optim="adamw_torch",
        save_steps=100,
        logging_steps=1,
        learning_rate=1e-4,
        fp16=False,
        bf16=False,
        max_grad_norm=0.3,
        num_train_epochs=1,
        warmup_ratio=0.03,
        group_by_length=True,
        lr_scheduler_type="cosine",
        dataloader_num_workers=2,
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

    print("Beginning CPU LoRA training...")
    trainer.train()

    print(f"Saving PEFT adapter to {OUTPUT_DIR}")
    trainer.model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)


if __name__ == "__main__":
    main()
