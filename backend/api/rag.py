from __future__ import annotations

import os
from typing import Any

import requests
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/rag-query", tags=["RAG"])


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_context_blocks(timeout_seconds: int) -> list[str]:
    context_blocks: list[str] = []

    news_api_key = os.getenv("NEWSAPI_KEY", "").strip()
    if news_api_key:
        try:
            res = requests.get(
                f"https://newsapi.org/v2/top-headlines?category=business&apiKey={news_api_key}",
                timeout=timeout_seconds,
            )
            if res.status_code == 200:
                articles = res.json().get("articles", [])[:3]
                headlines = [a.get("title") for a in articles if a.get("title")]
                if headlines:
                    context_blocks.append(f"Recent Financial News: {', '.join(headlines)}")
        except Exception:
            pass

    try:
        symbol = os.getenv("RAG_BINANCE_SYMBOL", "BTCUSDT").strip().upper() or "BTCUSDT"
        res = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=timeout_seconds,
        )
        if res.status_code == 200:
            price = res.json().get("price")
            if price:
                context_blocks.append(f"Live Market: {symbol}=$ {price}")
    except Exception:
        pass

    return context_blocks

@router.post("/")
def run_rag_query(payload: dict) -> dict[str, Any]:
    prompt = payload.get("prompt")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing prompt")

    # Context building is intentionally CPU-bound to keep GPU capacity for model generation.
    if _env_flag("RAG_CONTEXT_CPU_ONLY", default=True):
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    context_timeout = int(os.getenv("RAG_CONTEXT_TIMEOUT_SECONDS", "5"))
    context_device = os.getenv("RAG_CONTEXT_DEVICE", "cpu").strip().lower() or "cpu"
    if _env_flag("RAG_CONTEXT_CPU_ONLY", default=True):
        context_device = "cpu"

    # 1. Retrieval Phase: build external context using CPU-only networking + parsing.
    context_blocks = _build_context_blocks(timeout_seconds=context_timeout)

    joined_context = "\n".join(context_blocks)
    if not joined_context:
        joined_context = "No live external context could be retrieved."

    # 2. Augmentation Phase
    system_prompt = f"""You are BINFIN, a specialized AI trained to analyze financial data.
Current Live Context gathered via RAG:
===
{joined_context}
==="""

    ollama_url = os.getenv("RAG_OLLAMA_URL", os.getenv("OLLAMA_URL", "http://ollama:11434"))
    ollama_model = os.getenv("RAG_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "binfin-custom"))
    generation_timeout = int(os.getenv("RAG_GENERATION_TIMEOUT_SECONDS", "60"))
    
    # 3. Generation Phase
    try:
        ollama_payload = {
            "model": ollama_model,
            "prompt": prompt,
            "system": system_prompt,
            "stream": False,
        }
        res = requests.post(f"{ollama_url}/api/generate", json=ollama_payload, timeout=generation_timeout)
        res.raise_for_status()
        reply = res.json().get("response", "")
        return {
            "response": reply,
            "context_used": context_blocks,
            "runtime": {
                "context_device": context_device,
                "generation_model": ollama_model,
                "generation_backend": "ollama",
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama generation failed: {str(e)}")
