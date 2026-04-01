from __future__ import annotations

from datetime import UTC, datetime, timedelta

from processing.sentiment.aggregator import SentimentAggregator, SentimentPoint


def test_sentiment_aggregation_windowed_weighting() -> None:
    now = datetime.now(UTC)
    points = [
        SentimentPoint("BTCUSDT", "reddit", 0.7, 0.8, 1.0, now - timedelta(minutes=5)),
        SentimentPoint("BTCUSDT", "news", 0.4, 0.9, 1.2, now - timedelta(minutes=55)),
    ]

    out = SentimentAggregator().aggregate(points, now=now)
    assert "BTCUSDT" in out
    assert 0.0 <= out["BTCUSDT"]["1h"] <= 1.0
    assert out["BTCUSDT"]["24h"] != 0.5
