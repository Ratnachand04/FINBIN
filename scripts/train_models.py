from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from processing.prediction.main import PredictionEngine


def synthetic_price_frame(symbol: str, n: int = 500) -> pd.DataFrame:
    now = datetime.now(UTC)
    base_map = {
        "BTCUSDT": 70000.0,
        "ETHUSDT": 3500.0,
        "DOGEUSDT": 0.25,
    }
    base = base_map.get(symbol.upper(), 100.0)
    rows = []
    price = base
    for idx in range(n):
        ts = now - timedelta(minutes=(n - idx) * 15)
        drift = 0.0001 if idx % 3 else -0.00008
        price = price * (1 + drift)
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


def main() -> None:
    engine = PredictionEngine()
    for symbol in ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]:
        frame = synthetic_price_frame(symbol)
        pred = engine.train_and_predict(symbol, frame)
        print(
            f"{pred.symbol}: next={pred.next_price:.2f} "
            f"CI=({pred.low_95:.2f}, {pred.high_95:.2f})"
        )


if __name__ == "__main__":
    main()
