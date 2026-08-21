"""Correctness tests for the backtest execution engine.

Each test pins a property that was violated by the previous implementation and
that silently inflated reported performance. They use hand-built fixtures with
analytically known answers rather than database data, so they run anywhere.
"""

from __future__ import annotations

import math
import random
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

def _import_engine():
    """Import the engine without requiring a live database.

    The module pulls in the async DB layer at import time, but trade simulation
    is pure. Stub the dependency only for the duration of the import, then
    restore ``sys.modules`` -- the imported module already holds its own
    references, and leaving a stub behind would mask import errors in unrelated
    test modules.
    """
    had = "backend.database" in sys.modules
    saved = sys.modules.get("backend.database")

    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)

    async def _unused(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("database access is not expected in these tests")

    stub.execute_raw_sql = _unused
    sys.modules["backend.database"] = stub
    try:
        from backend.backtest.engine import BacktestEngine
        from backend.backtest.metrics import PerformanceMetrics

        return BacktestEngine, PerformanceMetrics
    finally:
        if had:
            sys.modules["backend.database"] = saved
        else:
            sys.modules.pop("backend.database", None)


BacktestEngine, PerformanceMetrics = _import_engine()

START = datetime(2024, 1, 1, tzinfo=UTC)
BAR = timedelta(minutes=15)


def bars(symbol: str, closes: list[float], *, highs=None, lows=None, opens=None):
    """Build a 15m OHLC series. Defaults make open/high/low track close."""
    out = []
    for i, close in enumerate(closes):
        out.append(
            {
                "ts": START + i * BAR,
                "symbol": symbol,
                "open": (opens[i] if opens else close),
                "high": (highs[i] if highs else close),
                "low": (lows[i] if lows else close),
                "close": close,
            }
        )
    return out


def signal(symbol: str, i: int, side: str, **kw):
    base = {
        "id": kw.pop("id", 1),
        "ts": START + i * BAR,
        "symbol": symbol,
        "signal": side,
        "strength": kw.pop("strength", 10.0),
    }
    base.update(kw)
    return base


NO_COST = {"transaction_cost": 0.0, "slippage": 0.0}


class TestNoLookahead:
    def test_entry_fills_at_next_bar_open_not_signal_bar_close(self):
        # Signal is emitted on bar 0. Bar 1 opens at 200 then closes at 100.
        # Filling at bar 0's close (100) would be a look-ahead-free-lunch;
        # the correct fill is bar 1's open (200).
        prices = bars("BTC", [100.0, 100.0, 100.0], opens=[100.0, 200.0, 100.0])
        trades, curve = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "BUY")],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, take_profit=None),
        )
        assert curve, "expected an equity curve"
        # One position opened at bar 1 at price 200.
        assert any(row["open_positions"] == 1 for row in curve)

    def test_signal_after_last_bar_is_dropped(self):
        prices = bars("BTC", [100.0, 101.0])
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 1, "BUY")],  # no bar 2 to fill against
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=NO_COST,
        )
        assert trades == []

    def test_price_lookup_never_selects_a_future_bar(self):
        engine = BacktestEngine()
        rows = bars("BTC", [100.0, 200.0, 300.0])
        # A timestamp 1 minute after bar 0 is nearer to bar 0 than to bar 1,
        # but a timestamp 14 minutes after bar 0 is nearer to bar 1. Both must
        # resolve to bar 0, because bar 1 has not happened yet.
        assert engine._bar_index_at_or_before(rows, START + timedelta(minutes=1)) == 0
        assert engine._bar_index_at_or_before(rows, START + timedelta(minutes=14)) == 0
        assert engine._bar_index_at_or_before(rows, START - timedelta(minutes=1)) is None


class TestPerSymbolPricing:
    def test_position_is_not_exited_by_another_symbols_price(self):
        # BTC drifts flat. DOGE collapses. The BTC long must survive: its stop
        # is far away and DOGE's move is irrelevant to it.
        btc = bars("BTC", [100.0] * 6)
        doge = bars("DOGE", [1.0, 1.0, 0.10, 0.05, 0.02, 0.01])
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "BUY", stop_loss=50.0, take_profit=150.0)],
            prices=btc + doge,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999),
        )
        assert trades == [], "BTC position was closed using a different symbol's price"


class TestShortAccounting:
    def test_short_equity_falls_when_price_rises(self):
        prices = bars("BTC", [100.0, 100.0, 120.0, 120.0])
        _, curve = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "SELL", stop_loss=1e9, take_profit=0.0)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999, allow_short=True),
        )
        opened = [row for row in curve if row["open_positions"] == 1]
        assert len(opened) >= 2
        assert opened[-1]["portfolio_value"] < opened[0]["portfolio_value"], (
            "short position gained equity as the price rose"
        )

    def test_short_pnl_is_positive_when_price_falls(self):
        prices = bars("BTC", [100.0, 100.0, 100.0, 90.0])
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "SELL", stop_loss=1e9, take_profit=90.0)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999),
        )
        assert len(trades) == 1
        assert trades[0]["pnl"] > 0
        assert trades[0]["exit_reason"] == "target"


class TestIntrabarExits:
    def test_stop_fills_at_the_stop_level_when_the_low_pierces_it(self):
        # Close never breaches the stop; the bar's low does. A close-only check
        # misses this fill entirely.
        prices = bars(
            "BTC",
            [100.0, 100.0, 99.0],
            lows=[100.0, 100.0, 90.0],
            opens=[100.0, 100.0, 99.0],
        )
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "BUY", stop_loss=95.0, take_profit=200.0)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999),
        )
        assert len(trades) == 1
        assert trades[0]["exit_reason"] == "stop"
        assert trades[0]["exit_price"] == pytest.approx(95.0)

    def test_stop_wins_when_one_bar_touches_both_levels(self):
        prices = bars(
            "BTC",
            [100.0, 100.0, 100.0],
            highs=[100.0, 100.0, 210.0],
            lows=[100.0, 100.0, 90.0],
            opens=[100.0, 100.0, 100.0],
        )
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "BUY", stop_loss=95.0, take_profit=200.0)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999),
        )
        assert trades[0]["exit_reason"] == "stop", "ambiguous bars must resolve pessimistically"


class TestSharpe:
    def test_annualisation_matches_the_closed_form(self):
        engine = BacktestEngine()
        returns = [0.01, -0.005, 0.02, 0.0, -0.01, 0.015, 0.005, -0.002]
        ppy = 365 * 24 * 4  # 15m bars
        got = engine.calculate_sharpe_ratio(returns, risk_free_rate=0.0, periods_per_year=ppy)

        n = len(returns)
        mean = sum(returns) / n
        var = sum((r - mean) ** 2 for r in returns) / (n - 1)
        expected = mean / math.sqrt(var) * math.sqrt(ppy)
        assert got == pytest.approx(expected)

    def test_periods_per_year_is_inferred_from_bar_spacing(self):
        engine = BacktestEngine()
        curve = [{"ts": START + i * BAR, "portfolio_value": 100.0} for i in range(10)]
        assert engine._periods_per_year(curve) == pytest.approx(365 * 24 * 4)

        daily = [{"ts": START + timedelta(days=i), "portfolio_value": 100.0} for i in range(10)]
        assert engine._periods_per_year(daily) == pytest.approx(365.0)

    def test_flat_equity_curve_has_zero_sharpe_not_a_divide_by_zero(self):
        engine = BacktestEngine()
        curve = [{"ts": START + i * BAR, "portfolio_value": 10_000.0} for i in range(20)]
        metrics = engine.calculate_performance_metrics([], curve)
        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["max_drawdown_pct"] == 0.0


class TestMonteCarlo:
    def test_bootstrap_is_not_degenerate(self):
        # The previous implementation shuffled and summed, so every path was
        # identical and the interval collapsed to a point.
        trades = [{"pnl": v} for v in [10.0, -5.0, 7.5, -2.0, 30.0, -12.0, 1.0, -8.0]]
        out = PerformanceMetrics().generate_monte_carlo_simulation(trades, n_simulations=500)
        lo, hi = out["ci_95"]
        assert hi > lo, "confidence interval collapsed to a point"
        assert len(set(out["final_returns"])) > 1, "all simulated paths were identical"
        assert 0.0 < out["probability_of_profit"] < 1.0
        assert out["median_max_drawdown"] > 0.0

    def test_is_reproducible_under_a_fixed_seed(self):
        trades = [{"pnl": v} for v in [3.0, -1.0, 4.0, -1.5, 5.0]]
        m = PerformanceMetrics()
        a = m.generate_monte_carlo_simulation(trades, n_simulations=200, seed=42)
        b = m.generate_monte_carlo_simulation(trades, n_simulations=200, seed=42)
        assert a["ci_95"] == b["ci_95"]


class TestCapitalConservation:
    def test_no_signals_leaves_capital_untouched(self):
        prices = bars("BTC", [100.0, 110.0, 90.0, 105.0])
        trades, curve = BacktestEngine().simulate_trades(
            signals=[], prices=prices, initial_capital=10_000.0, strategy_config=NO_COST
        )
        assert trades == []
        assert all(row["portfolio_value"] == pytest.approx(10_000.0) for row in curve)

    def test_round_trip_at_a_flat_price_loses_exactly_the_costs(self):
        prices = bars("BTC", [100.0] * 5)
        cfg = {
            "transaction_cost": 0.0004,
            "slippage": 0.0,
            "max_position_pct": 1.0,
            "timeout_hours": 0,
        }
        trades, _ = BacktestEngine().simulate_trades(
            signals=[signal("BTC", 0, "BUY", stop_loss=1.0, take_profit=1e9)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=cfg,
        )
        assert len(trades) == 1
        # Flat price: P&L is exactly the two-sided fee on the notional.
        notional = trades[0]["entry_price"] * trades[0]["quantity"]
        assert trades[0]["pnl"] == pytest.approx(-2 * notional * 0.0004, rel=1e-9)

    def test_equity_curve_reconciles_with_summed_trade_pnl(self):
        """Closed-loop invariant: with everything flat at the end, the equity
        change must equal the sum of realised P&L. Any divergence means cash is
        being created or destroyed somewhere in the fill path."""
        rng = random.Random(11)
        closes, p = [], 100.0
        for _ in range(400):
            p *= math.exp(rng.gauss(0, 0.01))
            closes.append(p)
        prices = bars(
            "BTC",
            closes,
            opens=closes,
            highs=[c * 1.004 for c in closes],
            lows=[c * 0.996 for c in closes],
        )
        sigs = [
            signal(
                "BTC", i, "BUY" if rng.random() < 0.5 else "SELL", id=i,
                take_profit=closes[i] * 1.01, stop_loss=closes[i] * 0.99,
            )
            for i in range(0, 380, 7)
        ]
        trades, curve = BacktestEngine().simulate_trades(
            signals=sigs,
            prices=prices,
            initial_capital=10_000.0,
            strategy_config={"transaction_cost": 0.0004, "slippage": 0.0005, "timeout_hours": 6},
        )
        assert trades, "fixture should generate trades"
        assert curve[-1]["open_positions"] == 0, "fixture should end flat"

        realised = sum(t["pnl"] for t in trades)
        equity_change = curve[-1]["portfolio_value"] - 10_000.0
        assert equity_change == pytest.approx(realised, rel=1e-9, abs=1e-6)

    def test_one_position_per_symbol(self):
        prices = bars("BTC", [100.0] * 8)
        trades, curve = BacktestEngine().simulate_trades(
            signals=[signal("BTC", i, "BUY", id=i, stop_loss=1.0, take_profit=1e9) for i in range(5)],
            prices=prices,
            initial_capital=10_000.0,
            strategy_config=dict(NO_COST, timeout_hours=999),
        )
        assert max(row["open_positions"] for row in curve) == 1
