from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SentimentResult(BaseModel):
    label: str = Field(pattern="^(BULLISH|BEARISH|NEUTRAL|FUD)$")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class FinBertAnalyzer:
    def __init__(self) -> None:
        self.pipeline: Any | None = None
        self.device: str = "cpu"
        self._load_model()

    def _load_model(self) -> None:
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer, pipeline

            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            model_name = "ProsusAI/finbert"
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self.pipeline = pipeline(
                "text-classification",
                model=model,
                tokenizer=tokenizer,
                device=0 if self.device == "cuda" else -1,
                truncation=True,
                max_length=512,
            )
        except Exception as exc:
            logger.warning("finbert load failed: %s", exc)
            self.pipeline = None

    def analyze_batch(self, texts: list[str]) -> list[SentimentResult]:
        if not self.pipeline:
            return [SentimentResult(label="NEUTRAL", confidence=0.5, reasoning="model_unavailable") for _ in texts]

        outputs = self.pipeline(texts)
        results: list[SentimentResult] = []
        for item in outputs:
            raw_label = str(item.get("label", "neutral")).lower()
            label = "NEUTRAL"
            if "positive" in raw_label:
                label = "BULLISH"
            elif "negative" in raw_label:
                label = "BEARISH"
            results.append(
                SentimentResult(
                    label=label,
                    confidence=float(item.get("score", 0.5)),
                    reasoning=f"finbert:{raw_label}",
                )
            )
        return results
