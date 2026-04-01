from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable


@dataclass
class SentimentPoint:
    symbol: str
    source: str
    score: float
    confidence: float
    volume_weight: float
    ts: datetime


class SentimentAggregator:
    WINDOW_SECONDS = {
        "15min": 15 * 60,
        "1h": 60 * 60,
        "4h": 4 * 60 * 60,
        "24h": 24 * 60 * 60,
    }

    def aggregate(self, points: Iterable[SentimentPoint], now: datetime | None = None) -> dict[str, dict[str, float]]:
        now = now or datetime.now(UTC)
        output: dict[str, dict[str, float]] = {}

        by_symbol: dict[str, list[SentimentPoint]] = {}
        for point in points:
            by_symbol.setdefault(point.symbol.upper(), []).append(point)

        for symbol, symbol_points in by_symbol.items():
            output[symbol] = {}
            for timeframe, seconds in self.WINDOW_SECONDS.items():
                scored = []
                for point in symbol_points:
                    age = max((now - point.ts).total_seconds(), 0)
                    if age > seconds:
                        continue
                    time_weight = 1 / (1 + age / max(seconds, 1))
                    vol_weight = max(point.volume_weight, 0.1)
                    weight = time_weight * vol_weight * max(point.confidence, 0.1)
                    scored.append((point.score, weight))

                if not scored:
                    output[symbol][timeframe] = 0.5
                else:
                    numerator = sum(score * weight for score, weight in scored)
                    denominator = sum(weight for _, weight in scored)
                    output[symbol][timeframe] = numerator / denominator if denominator else 0.5
        return output
