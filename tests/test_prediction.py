from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from processing.prediction.main import PredictionEngine


def _price_df(n: int = 120) -> pd.DataFrame:
    now = datetime.now(UTC)
    rows = []
    price = 100.0
    for idx in range(n):
        ts = now - timedelta(minutes=(n - idx) * 15)
        price = price * (1 + (0.001 if idx % 2 == 0 else -0.0007))
        rows.append(
            {
                "timestamp": ts,
                "open": price * 0.999,
                "high": price * 1.002,
                "low": price * 0.998,
                "close": price,
                "volume": 1000 + idx,
            }
        )
    return pd.DataFrame(rows)


def test_prediction_engine_returns_range() -> None:
    engine = PredictionEngine()
    out = engine.train_and_predict("BTCUSDT", _price_df())
    assert out.symbol == "BTCUSDT"
    assert out.low_95 <= out.next_price <= out.high_95
