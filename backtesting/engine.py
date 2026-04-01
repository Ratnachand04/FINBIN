from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.metrics import summary_stats


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity_curve: pd.Series
    metrics: dict[str, float]


class BacktestEngine:
    def run(self, prices: pd.DataFrame, signals: pd.DataFrame, initial_capital: float = 10000.0) -> BacktestResult:
        frame = prices.merge(signals[["timestamp", "side"]], on="timestamp", how="left")
        cash = initial_capital
        position = 0.0
        trades: list[dict[str, float | str]] = []
        equity_points: list[float] = []

        for _, row in frame.iterrows():
            price = float(row["close"])
            side = row.get("side")

            if side == "BUY" and cash > 0:
                position = cash / price
                cash = 0.0
                trades.append({"timestamp": str(row["timestamp"]), "side": "BUY", "price": price, "pnl": 0.0})
            elif side == "SELL" and position > 0:
                cash = position * price
                buy_trade = next((t for t in reversed(trades) if t["side"] == "BUY"), None)
                entry_price = float(buy_trade["price"]) if buy_trade else price
                pnl = (price - entry_price) * position
                position = 0.0
                trades.append({"timestamp": str(row["timestamp"]), "side": "SELL", "price": price, "pnl": pnl})

            equity = cash + position * price
            equity_points.append(equity)

        equity_curve = pd.Series(equity_points, index=pd.to_datetime(frame["timestamp"]))
        trades_df = pd.DataFrame(trades)
        metrics = summary_stats(trades_df, equity_curve)
        return BacktestResult(trades=trades_df, equity_curve=equity_curve, metrics=metrics)
