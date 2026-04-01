from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

SENTIMENT_REQUESTS = Counter("binfin_sentiment_requests_total", "Total sentiment analysis requests")
SENTIMENT_FALLBACKS = Counter("binfin_sentiment_fallback_total", "Total fallback calls to FinBERT")
SENTIMENT_LATENCY = Histogram(
    "binfin_sentiment_latency_seconds",
    "Sentiment model latency",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
SENTIMENT_GPU_ENABLED = Gauge("binfin_sentiment_gpu_enabled", "GPU availability for FinBERT")


class SentimentAnalyzer:
    def __init__(self) -> None:
        self.ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434")
        self.ollama_model = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct-q4_K_M")
        self.confidence_threshold = float(os.getenv("MIN_SENTIMENT_CONFIDENCE", "0.5"))
        self._transformers = self._load_optional("transformers")
        self._torch = self._load_optional("torch")
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._device: str = "cpu"
        self._source_weights = {
            "news": 1.2,
            "reddit": 1.0,
            "onchain": 1.1,
            "other": 0.9,
        }

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def analyze_with_ollama(self, text: str) -> dict[str, Any]:
        prompt = self.create_few_shot_prompt(text)
        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
        }

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(f"{self.ollama_url}/api/generate", json=payload)
                response.raise_for_status()
                result = response.json()
        finally:
            SENTIMENT_LATENCY.observe(time.perf_counter() - start)

        raw_output = result.get("response", "{}")
        parsed = self._parse_ollama_json(raw_output)
        return self._normalize_result(parsed, engine="ollama")

    async def analyze_with_finbert(self, text: str) -> dict[str, Any]:
        await self._ensure_finbert_loaded()
        if not self._transformers or not self._torch or self._tokenizer is None or self._model is None:
            raise RuntimeError("FinBERT dependencies are unavailable")

        start = time.perf_counter()
        try:
            encoded = self._tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            encoded = {k: v.to(self._device) for k, v in encoded.items()}
            with self._torch.no_grad():
                logits = self._model(**encoded).logits
                probs = self._torch.nn.functional.softmax(logits, dim=-1)[0]

            probs_list = probs.detach().cpu().tolist()
            label_id = int(probs.argmax().item())
            label_map = {0: "BEARISH", 1: "NEUTRAL", 2: "BULLISH"}
            sentiment = label_map.get(label_id, "NEUTRAL")
            confidence = float(max(probs_list))
            return self._normalize_result(
                {
                    "sentiment": sentiment,
                    "confidence": confidence,
                    "reasoning": "FinBERT classification probabilities",
                },
                engine="finbert",
            )
        finally:
            SENTIMENT_LATENCY.observe(time.perf_counter() - start)

    async def analyze(self, text: str, source_type: str = "reddit") -> dict[str, Any]:
        SENTIMENT_REQUESTS.inc()
        cleaned = self._clean_text(text)
        contextual_text = f"Source: {source_type}\nText: {cleaned}"

        try:
            result = await self.analyze_with_ollama(contextual_text)
        except Exception as exc:
            logger.warning("Ollama sentiment failed; using FinBERT fallback: %s", exc)
            SENTIMENT_FALLBACKS.inc()
            result = await self.analyze_with_finbert(cleaned)

        if float(result.get("confidence", 0.0)) < self.confidence_threshold:
            result["sentiment"] = "NEUTRAL"
            result["reasoning"] = f"Confidence below threshold ({self.confidence_threshold})"

        result["source_type"] = source_type
        result["text_length"] = len(cleaned)
        result["timestamp"] = datetime.now(UTC).isoformat()
        return result

    async def batch_analyze(self, texts: list[str], batch_size: int = 10) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(batch_size)
        done_counter = 0
        total = len(texts)

        async def _run(item: str) -> dict[str, Any]:
            nonlocal done_counter
            async with semaphore:
                result = await self.analyze(item)
                done_counter += 1
                logger.info("Sentiment batch progress: %s/%s", done_counter, total)
                return result

        tasks = [asyncio.create_task(_run(item)) for item in texts]
        return await asyncio.gather(*tasks)

    def create_few_shot_prompt(self, text: str) -> str:
        examples = [
            {
                "text": "BTC to the moon! Rocket gains incoming.",
                "sentiment": "BULLISH",
                "confidence": 0.95,
                "reasoning": "Strong positive momentum language",
            },
            {
                "text": "Ethereum gas fees are improving and dev activity is strong.",
                "sentiment": "BULLISH",
                "confidence": 0.88,
                "reasoning": "Positive fundamentals and ecosystem growth",
            },
            {
                "text": "I am not sure where the market is heading this week.",
                "sentiment": "NEUTRAL",
                "confidence": 0.70,
                "reasoning": "No directional conviction",
            },
            {
                "text": "Sideways chop continues with low volume.",
                "sentiment": "NEUTRAL",
                "confidence": 0.76,
                "reasoning": "Range-bound market behavior",
            },
            {
                "text": "This token is a scam and everyone should exit now.",
                "sentiment": "BEARISH",
                "confidence": 0.93,
                "reasoning": "Explicit negative and exit language",
            },
            {
                "text": "Massive sell pressure from whales; breakdown likely.",
                "sentiment": "BEARISH",
                "confidence": 0.91,
                "reasoning": "Strong downside signal",
            },
            {
                "text": "Rumors of exchange insolvency are spreading fast.",
                "sentiment": "FUD",
                "confidence": 0.87,
                "reasoning": "Fear and uncertainty driven narrative",
            },
            {
                "text": "SEC headlines causing panic across altcoins.",
                "sentiment": "FUD",
                "confidence": 0.84,
                "reasoning": "Regulatory fear dominates sentiment",
            },
            {
                "text": "Accumulation phase with consistent higher lows.",
                "sentiment": "BULLISH",
                "confidence": 0.81,
                "reasoning": "Constructive technical structure",
            },
            {
                "text": "Nothing changed fundamentally, just noise.",
                "sentiment": "NEUTRAL",
                "confidence": 0.65,
                "reasoning": "No substantive bullish or bearish signal",
            },
        ]

        examples_text = "\n\n".join(
            [
                "Text: \"{text}\"\nSentiment: {sentiment}\nConfidence: {confidence}\nReasoning: {reasoning}".format(
                    text=item["text"],
                    sentiment=item["sentiment"],
                    confidence=item["confidence"],
                    reasoning=item["reasoning"],
                )
                for item in examples
            ]
        )

        return (
            "You are a crypto sentiment analyst. "
            "Classify the text into one of: BULLISH, BEARISH, NEUTRAL, FUD. "
            "Return JSON with keys sentiment, confidence, reasoning.\n\n"
            f"Examples:\n{examples_text}\n\n"
            f"Text: \"{text}\"\n"
            "Output JSON only."
        )

    def calculate_aggregate_sentiment(
        self,
        results: list[dict[str, Any]],
        time_window: str = "24h",
    ) -> dict[str, Any]:
        if not results:
            return {
                "aggregate_score": 0.5,
                "label": "NEUTRAL",
                "count": 0,
                "window": time_window,
            }

        now = datetime.now(UTC)
        sentiment_map = {"BEARISH": 0.1, "FUD": 0.2, "NEUTRAL": 0.5, "BULLISH": 0.9}
        total_weight = 0.0
        weighted_sum = 0.0

        for item in results:
            sentiment = str(item.get("sentiment", "NEUTRAL")).upper()
            confidence = float(item.get("confidence", 0.5))
            source = str(item.get("source_type", "other")).lower()
            upvotes = float(item.get("upvotes", item.get("score", 1)) or 1)
            ts_raw = item.get("timestamp")

            ts = now
            if isinstance(ts_raw, str):
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                except Exception:
                    ts = now

            age_hours = max((now - ts).total_seconds() / 3600.0, 0.0)
            time_weight = 1 / (1 + age_hours)
            source_weight = self._source_weights.get(source, 1.0)
            volume_weight = max(1.0, min(upvotes, 5000.0)) ** 0.2
            weight = confidence * source_weight * time_weight * volume_weight

            weighted_sum += sentiment_map.get(sentiment, 0.5) * weight
            total_weight += weight

        score = weighted_sum / total_weight if total_weight > 0 else 0.5
        if score >= 0.66:
            label = "BULLISH"
        elif score <= 0.34:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "aggregate_score": score,
            "label": label,
            "count": len(results),
            "window": time_window,
        }

    async def _ensure_finbert_loaded(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        if not self._transformers or not self._torch:
            raise RuntimeError("transformers/torch dependencies are required for FinBERT")

        AutoTokenizer = getattr(self._transformers, "AutoTokenizer")
        AutoModelForSequenceClassification = getattr(self._transformers, "AutoModelForSequenceClassification")
        if not AutoTokenizer or not AutoModelForSequenceClassification:
            raise RuntimeError("transformers package is missing required classes")

        model_name = os.getenv("FINBERT_MODEL_NAME", "ProsusAI/finbert")
        self._device = "cuda" if bool(self._torch.cuda.is_available()) and os.getenv("ENABLE_GPU", "false").lower() == "true" else "cpu"
        SENTIMENT_GPU_ENABLED.set(1 if self._device == "cuda" else 0)

        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self._model.to(self._device)
        self._model.eval()

    def _clean_text(self, text: str) -> str:
        value = text or ""
        value = re.sub(r"https?://\\S+", " ", value)
        value = re.sub(r"[^A-Za-z0-9\s\.,!\?\-:$]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value[:4000]

    def _parse_ollama_json(self, raw_output: str) -> dict[str, Any]:
        try:
            return json.loads(raw_output)
        except json.JSONDecodeError:
            start = raw_output.find("{")
            end = raw_output.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(raw_output[start : end + 1])
                except Exception:
                    pass
        return {"sentiment": "NEUTRAL", "confidence": 0.5, "reasoning": "Unable to parse model output"}

    def _normalize_result(self, payload: dict[str, Any], engine: str) -> dict[str, Any]:
        sentiment = str(payload.get("sentiment", "NEUTRAL")).upper()
        if sentiment not in {"BULLISH", "BEARISH", "NEUTRAL", "FUD"}:
            sentiment = "NEUTRAL"

        confidence = payload.get("confidence", 0.5)
        try:
            confidence_value = float(confidence)
        except Exception:
            confidence_value = 0.5

        confidence_value = max(0.0, min(confidence_value, 1.0))
        reasoning = str(payload.get("reasoning", "No reasoning provided"))

        return {
            "sentiment": sentiment,
            "confidence": confidence_value,
            "reasoning": reasoning,
            "engine": engine,
            "timestamp": datetime.now(UTC).isoformat(),
        }
