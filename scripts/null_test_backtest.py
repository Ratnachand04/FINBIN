"""Randomised null test for the backtest engine.

Random entry signals are run against driftless geometric random walks. There is
no predictable structure by construction, so a correct engine charging positive
costs must lose money at a rate set by turnover and cost, and nothing else.

Why this is run over many seeds
-------------------------------
A single 75-day fixture has SE(Sharpe) of roughly sqrt(1/0.2) = 2.2. Asserting
``Sharpe <= 0`` on one path is a stochastic claim dressed as a deterministic
one: a correct engine fails it a substantial fraction of the time, and a single
observed value of -2.6 is about one standard error from zero and therefore
evidence of very little. This version runs N independent seeds and tests the
*distribution*, which is what makes the assertion calibrated.

Three assertions, in increasing order of strength:

1. Mean Sharpe across seeds is significantly negative (t-test on the seed mean).
2. Cash conservation: with everything flat at the end, the change in equity
   equals the sum of realised P&L, exactly. This is violated by any defect that
   creates or destroys cash and cannot be satisfied by accident.
3. Realised cost per trade matches the modelled round-trip cost.

Note on coverage: this exercises the *engine* with exogenous signals. It is
blind to feature leakage, split leakage and imputation leakage, which live
upstream of it. It is a necessary check, not a sufficient one.

Usage:
    python scripts/null_test_backtest.py [--seeds N] [--bars N]
"""

from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _import_engine():
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
FEE, SLIP = 0.0004, 0.0005


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
            prices.append({
                "ts": START + i * BAR, "symbol": symbol, "open": open_p,
                "high": max(open_p, price) * (1 + wick),
                "low": min(open_p, price) * (1 - wick), "close": price,
            })
            if rng.random() < 0.02:
                side = "BUY" if rng.random() < 0.5 else "SELL"
                signals.append({
                    "id": len(signals), "ts": START + i * BAR, "symbol": symbol,
                    "signal": side, "strength": rng.uniform(4.0, 10.0),
                    "take_profit": price * (1.02 if side == "BUY" else 0.98),
                    "stop_loss": price * (0.985 if side == "BUY" else 1.015),
                })
    return prices, signals


def run_one(n_bars: int, seed: int) -> dict:
    prices, signals = build_random_walk(n_bars, seed)
    engine = BacktestEngine()
    cfg = {"transaction_cost": FEE, "slippage": SLIP}
    trades, curve = engine.simulate_trades(signals, prices, 10_000.0, cfg)
    metrics = engine.calculate_performance_metrics(trades, curve)

    open_at_end = curve[-1]["open_positions"] if curve else 0
    realised = sum(t["pnl"] for t in trades)
    equity_change = (curve[-1]["portfolio_value"] - 10_000.0) if curve else 0.0
    cost_per_trade = (
        sum(t["fee"] for t in trades) / sum(t["entry_price"] * t["quantity"] for t in trades)
        if trades else 0.0
    )
    return {
        "seed": seed, "sharpe": metrics["sharpe_ratio"], "trades": metrics["total_trades"],
        "net_per_trade": metrics["avg_return_per_trade"],
        "open_at_end": open_at_end,
        "reconcile_error": abs(equity_change - realised),
        "realised_cost_rate": cost_per_trade,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--bars", type=int, default=2400)
    args = ap.parse_args()

    runs = [run_one(args.bars, s) for s in range(args.seeds)]
    sharpes = [r["sharpe"] for r in runs]
    mean_sr = statistics.mean(sharpes)
    sd_sr = statistics.stdev(sharpes) if len(sharpes) > 1 else 0.0
    se = sd_sr / math.sqrt(len(sharpes)) if sharpes else 0.0
    t = mean_sr / se if se > 0 else 0.0
    frac_pos = sum(1 for s in sharpes if s > 0) / len(sharpes)

    # Fees are booked on the trade; slippage is applied to the fill price and
    # therefore shows up in P&L, not in the fee field. Comparing the realised
    # fee rate against fee+slippage conflates the two.
    expected_fee = 2 * FEE
    expected_cost = 2 * (FEE + SLIP)
    worst_recon = max(r["reconcile_error"] for r in runs)
    flat = [r for r in runs if r["open_at_end"] == 0]
    worst_recon_flat = max((r["reconcile_error"] for r in flat), default=0.0)
    mean_cost = statistics.mean(r["realised_cost_rate"] for r in runs)

    print(f"seeds                : {args.seeds} x {args.bars} bars x {len(SYMBOLS)} symbols")
    print(f"mean Sharpe          : {mean_sr:+.3f}  (sd {sd_sr:.3f}, se {se:.3f})")
    print(f"t-stat vs zero       : {t:+.2f}")
    print(f"seeds with SR > 0    : {frac_pos:.1%}   <- a single-path assertion would flake this often")
    print(f"Sharpe range         : [{min(sharpes):+.2f}, {max(sharpes):+.2f}]")
    print(f"mean net/trade       : {statistics.mean(r['net_per_trade'] for r in runs)*100:+.4f}%")
    print(f"modelled round trip  : {expected_cost*100:.4f}%  (fees {expected_fee*100:.4f}% + slippage in price)")
    print(f"realised fee rate    : {mean_cost*100:.4f}% of entry notional (fees only)")
    print(f"reconciliation error : max {worst_recon:.3e} (all), {worst_recon_flat:.3e} (flat at end)")
    print(f"runs flat at end     : {len(flat)}/{len(runs)}")

    failures = []
    if t > -3.0:
        failures.append(f"mean Sharpe {mean_sr:+.3f} not significantly negative (t={t:+.2f}); "
                        "engine may be manufacturing edge")
    if worst_recon_flat > 1e-6:
        failures.append(f"cash not conserved: max |equity change - sum P&L| = {worst_recon_flat:.3e}")
    if not (0.9 * expected_fee <= mean_cost <= 1.1 * expected_fee):
        failures.append(f"realised fee rate {mean_cost:.6f} inconsistent with modelled {expected_fee:.6f}")

    if failures:
        print("\nFAIL")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\nPASS: no edge on structureless data, cash conserved, costs reconcile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
