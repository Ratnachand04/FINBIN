from __future__ import annotations

import math

import pandas as pd


def max_drawdown(equity_curve: pd.Series) -> float:
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    return float(drawdown.min()) if not drawdown.empty else 0.0


def sharpe_ratio(returns: pd.Series, periods_per_year: int = 365 * 24 * 4) -> float:
    if returns.empty:
        return 0.0
    vol = returns.std()
    if vol == 0 or math.isnan(vol):
        return 0.0
    return float((returns.mean() / vol) * math.sqrt(periods_per_year))


def summary_stats(trades: pd.DataFrame, equity_curve: pd.Series) -> dict[str, float]:
    returns = equity_curve.pct_change().dropna()
    win_rate = float((trades["pnl"] > 0).mean()) if not trades.empty else 0.0
    profit_factor = 0.0
    if not trades.empty:
        gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum())
        gross_loss = abs(float(trades.loc[trades["pnl"] < 0, "pnl"].sum()))
        profit_factor = (gross_profit / gross_loss) if gross_loss else gross_profit

    return {
        "total_return": float((equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1) if len(equity_curve) > 1 else 0.0,
        "max_drawdown": max_drawdown(equity_curve),
        "sharpe": sharpe_ratio(returns),
        "win_rate": win_rate,
        "profit_factor": float(profit_factor),
    }
