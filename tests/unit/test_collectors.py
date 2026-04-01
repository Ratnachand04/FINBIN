from __future__ import annotations

from datetime import UTC, datetime

import pytest

try:
    from backend.collectors.news_collector import NewsCollector
    from backend.collectors.onchain_collector import OnchainCollector
    from backend.collectors.reddit_collector import RedditCollector
except Exception as exc:  # pragma: no cover - environment bootstrap guard
    pytest.skip(f"collector imports unavailable: {exc}", allow_module_level=True)


def test_reddit_collector_extract_coins() -> None:
    collector = RedditCollector()
    text = "BTC and Ethereum look strong, while SOL might follow. $ADA also mentioned."
    coins = collector.extract_coin_mentions(text)
    assert "BTC" in coins
    assert "ETH" in coins
    assert "SOL" in coins
    assert "ADA" in coins


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


@pytest.mark.asyncio
async def test_onchain_value_calculation(monkeypatch: pytest.MonkeyPatch) -> None:
    collector = OnchainCollector()

    async def _mock_price(token: str, timestamp: datetime | str | None) -> float:
        assert token == "BTC"
        return 50000.0

    monkeypatch.setattr(collector, "_get_historical_price", _mock_price)
    value = await collector.calculate_value_usd(amount=0.25, token="BTC", timestamp=datetime.now(UTC))
    assert value == pytest.approx(12500.0)

