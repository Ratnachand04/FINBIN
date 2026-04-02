from __future__ import annotations

import asyncio
import logging

from data_ingestion.collectors.binance_collector import BinanceCollector
from data_ingestion.collectors.reddit_collector import RedditCollector

logger = logging.getLogger(__name__)


async def run_collectors() -> None:
    reddit = RedditCollector()
    binance = BinanceCollector()

    tasks = [
        asyncio.create_task(reddit.stream_posts()),
        asyncio.create_task(binance.stream_ohlcv()),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    try:
        asyncio.run(run_collectors())
    except KeyboardInterrupt:
        logger.info("collectors stopped")
