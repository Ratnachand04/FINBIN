from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from processing.sentiment.finbert_analyzer import FinBertAnalyzer
from processing.sentiment.ollama_analyzer import OllamaAnalyzer

logger = logging.getLogger(__name__)


async def run_sentiment_loop() -> None:
    ollama = OllamaAnalyzer()
    finbert = FinBertAnalyzer()

    while True:
        try:
            texts = [
                "Bitcoin sees growing inflows and strong market breadth",
                "Whales moved funds to exchanges, risk sentiment weakens",
            ]
            try:
                results = await ollama.analyze_batch(texts, batch_size=100)
            except Exception:
                results = finbert.analyze_batch(texts)
            logger.info("processed sentiment batch size=%s", len(results))
        except Exception as exc:
            logger.exception("sentiment loop error: %s", exc)
        await asyncio.sleep(60)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    asyncio.run(run_sentiment_loop())
