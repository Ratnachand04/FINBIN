from __future__ import annotations

import asyncio
import logging

from data_ingestion.collectors.binance_collector import BinanceCollector
from data_ingestion.collectors.etherscan_collector import EtherscanCollector
from data_ingestion.collectors.reddit_collector import RedditCollector

logger = logging.getLogger(__name__)


async def run_collectors() -> None:
    reddit = RedditCollector()
    binance = BinanceCollector()
    etherscan = EtherscanCollector()

    tasks = [
        asyncio.create_task(reddit.stream_posts()),
        asyncio.create_task(binance.stream_ohlcv("BTCUSDT")),
        asyncio.create_task(etherscan.poll_whale_transactions("0xbtc")),
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    try:
        asyncio.run(run_collectors())
    except KeyboardInterrupt:
        logger.info("collectors stopped")
