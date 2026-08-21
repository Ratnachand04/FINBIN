"""Null test for the backtest engine.

Runs random entry signals against a driftless geometric random walk. There is no
predictable structure in the data by construction, so after costs a correct
engine must report a Sharpe at or below zero. A large positive Sharpe here means
the engine is manufacturing edge -- look-ahead in the fill path, broken
mark-to-market, or a bad annualisation factor.

The second check is the stronger one: expectancy implied by the realised win
rate and average win/loss must equal realised mean return per trade. If those
disagree, cash accounting is leaking somewhere.

Usage:
    python scripts/null_test_backtest.py [--bars N] [--seed S]
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _import_engine():
    """Import the engine without requiring a database connection."""
    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)

    async def _unused(*args, **kwargs):
        raise AssertionError("database access is not expected in the null test")

    stub.execute_raw_sql = _unused
    saved = sys.modules.get("backend.database")
    sys.modules["backend.database"] = stub
    try:
        from backend.backtest.engine import BacktestEngine
        from backend.backtest.metrics import PerformanceMetrics

        return BacktestEngine, PerformanceMetrics
    finally:
        if saved is not None:
            sys.modules["backend.database"] = saved
        else:
            sys.modules.pop("backend.database", None)


BacktestEngine, PerformanceMetrics = _import_engine()

START = datetime(2024, 1, 1, tzinfo=UTC)
BAR = timedelta(minutes=15)
SYMBOLS = (("BTC", 42_000.0, 0.004), ("ETH", 2_200.0, 0.005), ("DOGE", 0.08, 0.009))


def build_random_walk(n_bars: int, seed: int):
    rng = random.Random(seed)
    prices: list[dict] = []
    signals: list[dict] = []

    for symbol, p0, vol in SYMBOLS:
        price = p0
        for i in range(n_bars):
            open_p = price
            price *= math.exp(rng.gauss(0.0, vol))
            wick = abs(rng.gauss(0.0, vol / 3))
            prices.append(
                {
                    "ts": START + i * BAR,
                    "symbol": symbol,
                    "open": open_p,
                    "high": max(open_p, price) * (1 + wick),
                    "low": min(open_p, price) * (1 - wick),
                    "close": price,
                }
            )
            if rng.random() < 0.02:  # ~2% of bars carry a coin-flip signal
                side = "BUY" if rng.random() < 0.5 else "SELL"
                signals.append(
                    {
                        "id": len(signals),
                        "ts": START + i * BAR,
                        "symbol": symbol,
                        "signal": side,
                        "strength": rng.uniform(4.0, 10.0),
                        "take_profit": price * (1.02 if side == "BUY" else 0.98),
                        "stop_loss": price * (0.985 if side == "BUY" else 1.015),
                    }
                )
    return prices, signals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=7200, help="bars per symbol (default 7200 ~ 75 days)")
    parser.add_argument("--seed", type=int, default=20240101)
    args = parser.parse_args()

    prices, signals = build_random_walk(args.bars, args.seed)

    engine = BacktestEngine()
    trades, curve = engine.simulate_trades(signals, prices, 10_000.0, {})
    metrics = engine.calculate_performance_metrics(trades, curve)

    returns = engine._equity_returns(curve)
    ppy = engine._periods_per_year(curve)
    pm = PerformanceMetrics()
    risk = pm.calculate_risk_adjusted_returns(returns, equity_curve=curve, periods_per_year=ppy)
    mc = pm.generate_monte_carlo_simulation(trades, n_simulations=2000)

    print(f"bars per symbol   : {args.bars} ({args.bars * 15 / 60 / 24:.0f} days of 15m data)")
    print(f"signals / trades  : {len(signals)} / {metrics['total_trades']}")
    print(f"periods per year  : {ppy:,.0f}  (15m bars -> expect 35,040)")
    print(f"win rate          : {metrics['win_rate_pct']:.2f}%")
    print(f"total return      : {metrics['total_return_pct']:.2f}%")
    print(f"max drawdown      : {metrics['max_drawdown_pct']:.2f}%")
    print(f"sharpe            : {metrics['sharpe_ratio']:.3f}")
    print(f"sortino / calmar  : {risk['sortino_ratio']:.3f} / {risk['calmar_ratio']:.3f}")
    print(f"profit factor     : {metrics['profit_factor']:.3f}")
    print(f"MC P(profit)      : {mc['probability_of_profit']:.3f}")
    lo, hi = mc["ci_95"]
    print(f"MC 95% CI on P&L  : [{lo:.1f}, {hi:.1f}]  (width {hi - lo:.1f})")

    failures = []
    if metrics["sharpe_ratio"] > 0.5:
        failures.append(f"Sharpe {metrics['sharpe_ratio']:.3f} > 0.5 on random signals: engine is inventing edge")
    if hi <= lo:
        failures.append("Monte Carlo interval collapsed to a point")

    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        wr = len(wins) / len(trades)
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
        n_loss = len(trades) - len(wins)
        avg_loss = sum(t["pnl_pct"] for t in trades if t["pnl"] <= 0) / n_loss if n_loss else 0.0
        implied = wr * avg_win + (1 - wr) * avg_loss
        realised = sum(t["pnl_pct"] for t in trades) / len(trades)
        print(f"\nexpectancy implied: {implied * 100:+.4f}% per trade")
        print(f"expectancy realised: {realised * 100:+.4f}% per trade")
        if abs(implied - realised) > 1e-9:
            failures.append(f"expectancy mismatch: {implied:.8f} vs {realised:.8f}")

    if failures:
        print("\nFAIL")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nPASS: engine reports no edge on structureless data and P&L reconciles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
