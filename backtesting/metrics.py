from __future__ import annotations

import math

import pandas as pd


def max_drawdown(equity_curve: pd.Series) -> float:
    roll_max = equity_curve.cummax()
    drawdown = (equity_curve - roll_max) / roll_max
    return float(drawdown.min()) if not drawdown.empty else 0.0


SECONDS_PER_YEAR = 365 * 24 * 3600
DEFAULT_BAR_SECONDS = 900  # 15m


def infer_periods_per_year(index: pd.Index) -> float:
    """Derive the annualisation factor from the actual sample spacing.

    Hardcoding a factor (this module previously assumed 15m bars unconditionally)
    silently mis-annualises any series sampled at a different cadence, which is
    the fastest way to manufacture an implausible Sharpe.
    """
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 3:
        return SECONDS_PER_YEAR / DEFAULT_BAR_SECONDS
    deltas = index.to_series().diff().dt.total_seconds().dropna()
    deltas = deltas[deltas > 0]
    if deltas.empty:
        return SECONDS_PER_YEAR / DEFAULT_BAR_SECONDS
    return float(SECONDS_PER_YEAR / deltas.median())


def sharpe_ratio(returns: pd.Series, periods_per_year: float | None = None) -> float:
    if len(returns) < 2:
        return 0.0
    if periods_per_year is None:
        periods_per_year = infer_periods_per_year(returns.index)
    vol = returns.std(ddof=1)
    if vol == 0 or math.isnan(vol) or periods_per_year <= 0:
        return 0.0
    return float((returns.mean() / vol) * math.sqrt(periods_per_year))


def summary_stats(trades: pd.DataFrame, equity_curve: pd.Series) -> dict[str, float]:
    returns = equity_curve.pct_change().replace([float("inf"), float("-inf")], pd.NA).dropna()
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
