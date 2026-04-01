from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from config.settings import get_settings

logger = logging.getLogger(__name__)


class SentimentResult(BaseModel):
    label: str = Field(pattern="^(BULLISH|BEARISH|NEUTRAL|FUD)$")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class OllamaAnalyzer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.prompt_prefix = (
            "You are a crypto sentiment classifier. "
            "Classify text into BULLISH, BEARISH, NEUTRAL, FUD. "
            "Return JSON keys: label, confidence, reasoning."
        )

    async def analyze_one(self, text: str) -> SentimentResult:
        payload = {
            "model": self.settings.ollama_model,
            "prompt": f"{self.prompt_prefix}\n\nText: {text[:2500]}",
            "stream": False,
            "format": "json",
        }
        timeout = httpx.Timeout(self.settings.ollama_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{self.settings.ollama_url}/api/generate", json=payload)
            response.raise_for_status()
            raw = response.json().get("response", "{}")

        parsed = self._parse_output(raw)
        return SentimentResult(**parsed)

    async def analyze_batch(self, posts: list[str], batch_size: int = 50) -> list[SentimentResult]:
        outputs: list[SentimentResult] = []
        semaphore = asyncio.Semaphore(8)

        async def _runner(text: str) -> SentimentResult:
            async with semaphore:
                try:
                    return await self.analyze_one(text)
                except Exception as exc:
                    logger.warning("ollama analyze failed: %s", exc)
                    return SentimentResult(label="NEUTRAL", confidence=0.5, reasoning="fallback")

        for idx in range(0, len(posts), batch_size):
            chunk = posts[idx : idx + batch_size]
            outputs.extend(await asyncio.gather(*[_runner(text) for text in chunk]))
        return outputs

    def _parse_output(self, raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
            label = str(payload.get("label", "NEUTRAL")).upper()
            return {
                "label": label if label in {"BULLISH", "BEARISH", "NEUTRAL", "FUD"} else "NEUTRAL",
                "confidence": float(payload.get("confidence", 0.5)),
                "reasoning": str(payload.get("reasoning", ""))[:500],
            }
        except Exception:
            return {"label": "NEUTRAL", "confidence": 0.5, "reasoning": "parse_failure"}
