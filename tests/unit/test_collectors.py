from __future__ import annotations

from datetime import UTC, datetime

import pytest

try:
    from backend.collectors.news_collector import NewsCollector
    from backend.collectors.reddit_collector import RedditCollector
except Exception as exc:  # pragma: no cover - environment bootstrap guard
    pytest.skip(f"collector imports unavailable: {exc}", allow_module_level=True)


def test_reddit_collector_extract_coins() -> None:
    collector = RedditCollector()
    text = "Bitcoin and Ethereum are rising while Dogecoin volatility stays high."
    coins = collector.extract_coin_mentions(text)
    assert "BTC" in coins
    assert "ETH" in coins
    assert "DOGE" in coins


def test_news_deduplication() -> None:
    collector = NewsCollector()
    now = datetime.now(UTC)
    articles = [
        {
            "url_hash": "hash1",
            "title": "Bitcoin ETF sees strong inflows",
            "content": "Large inflows support bullish momentum.",
            "url": "https://example.com/a",
            "source_name": "src",
            "author": "auth",
            "description": "desc",
            "published_at": now,
            "ts": now,
            "mentioned_coins": ["BTC"],
            "extracted_entities": {},
            "sentiment_score": None,
            "metadata": {},
            "created_at": now,
        },
        {
            "url_hash": "hash1",
            "title": "Bitcoin ETF sees strong inflows",
            "content": "Large inflows support bullish momentum.",
            "url": "https://example.com/a",
            "source_name": "src",
            "author": "auth",
            "description": "desc",
            "published_at": now,
            "ts": now,
            "mentioned_coins": ["BTC"],
            "extracted_entities": {},
            "sentiment_score": None,
            "metadata": {},
            "created_at": now,
        },
    ]
    deduped = collector.deduplicate_articles(articles)
    assert len(deduped) == 1

