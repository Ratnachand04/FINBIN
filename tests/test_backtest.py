from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from backtesting.engine import BacktestEngine


def test_backtest_engine_outputs_metrics() -> None:
    now = datetime.now(UTC)
    prices = pd.DataFrame(
        [
            {"timestamp": now - timedelta(minutes=30), "close": 100.0},
            {"timestamp": now - timedelta(minutes=15), "close": 102.0},
            {"timestamp": now, "close": 101.0},
        ]
    )
    signals = pd.DataFrame(
        [
            {"timestamp": now - timedelta(minutes=30), "side": "BUY"},
            {"timestamp": now, "side": "SELL"},
        ]
    )

    result = BacktestEngine().run(prices, signals)
    assert "sharpe" in result.metrics
    assert len(result.equity_curve) == len(prices)
