"""Pytest plugin that reintroduces a single known defect before collection.

Selected by the BINFIN_MUTATION environment variable. Used by
scripts/mutation_matrix.py to demonstrate that the regression suite actually
detects each defect rather than merely accompanying the fix.

A test count is a claim about effort. A mutation score is a claim about
coverage, and it is the one worth reporting.
"""

from __future__ import annotations

import math
import os
import random
import sys
import types


def _import_targets():
    stub = types.ModuleType("backend.database")
    stub.db_manager = types.SimpleNamespace(session_factory=None, redis_client=None)

    async def _unused(*a, **k):
        raise AssertionError("no db")

    stub.execute_raw_sql = _unused
    saved = sys.modules.get("backend.database")
    sys.modules["backend.database"] = stub
    try:
        from backend.backtest.engine import BacktestEngine
        from backend.backtest.metrics import PerformanceMetrics
        from backend.ml.feature_engineer import FeatureEngineer
        from backend.ml.model_trainer import ModelTrainer

        return BacktestEngine, PerformanceMetrics, FeatureEngineer, ModelTrainer
    finally:
        if saved is not None:
            sys.modules["backend.database"] = saved
        else:
            sys.modules.pop("backend.database", None)


# --------------------------------------------------------------------------
# Individual defect reintroductions
# --------------------------------------------------------------------------
def _d2_lookahead(Engine, *_):
    """Nearest bar by absolute time distance, which can select a future bar."""
    def _bar_index_at_or_before(self, rows, ts):
        if not rows:
            return None
        best, best_d = None, None
        for i, row in enumerate(rows):
            d = abs((row["ts"] - ts).total_seconds())
            if best_d is None or d < best_d:
                best, best_d = i, d
        return best
    Engine._bar_index_at_or_before = _bar_index_at_or_before


def _d4_short_sign(Engine, *_):
    """Mark every position long: a short gains equity as price rises."""
    def _equity(self, cash, positions, bars_now, bars_by_symbol, cursor):
        total = cash
        for symbol, pos in positions.items():
            bar = bars_now.get(symbol)
            if bar is None:
                rows = bars_by_symbol.get(symbol, [])
                i = cursor.get(symbol, 0) - 1
                bar = rows[i] if 0 <= i < len(rows) else None
            mark = float(bar.get("close") or 0.0) if bar else pos["entry_price"]
            total += mark * pos["quantity"]
        return total
    Engine._equity = _equity


def _d5_close_only_exits(Engine, *_):
    """Check stop and target against the close only, ignoring the bar range."""
    def _check_exit_conditions(self, position, ts, bar, timeout_hours):
        from datetime import timedelta
        if ts == position["entry_time"]:
            return None
        close = float(bar.get("close") or 0.0)
        if close <= 0:
            return None
        side, target, stop = position["side"], float(position["target"]), float(position["stop"])
        if side == "BUY":
            if close >= target:
                return {"reason": "target", "price": close}
            if close <= stop:
                return {"reason": "stop", "price": close}
        else:
            if close <= target:
                return {"reason": "target", "price": close}
            if close >= stop:
                return {"reason": "stop", "price": close}
        if ts - position["entry_time"] >= timedelta(hours=timeout_hours):
            return {"reason": "timeout", "price": close}
        return None
    Engine._check_exit_conditions = _check_exit_conditions


def _d6_sharpe_annualisation(Engine, *_):
    """Annualise per-trade returns by a fixed sqrt(252)."""
    original = Engine.calculate_performance_metrics

    def calculate_performance_metrics(self, trades, portfolio_values):
        out = original(self, trades, portfolio_values)
        rets = [float(t.get("pnl_pct", 0.0)) for t in trades]
        if len(rets) >= 2:
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            sd = math.sqrt(var)
            out["sharpe_ratio"] = (mean / sd * math.sqrt(252)) if sd > 0 else 0.0
        return out

    Engine.calculate_performance_metrics = calculate_performance_metrics

    def calculate_sharpe_ratio(self, returns, risk_free_rate=0.02, periods_per_year=252.0):
        if len(returns) < 2:
            return 0.0
        rf = risk_free_rate / 252
        ex = [r - rf for r in returns]
        mean = sum(ex) / len(ex)
        var = sum((r - mean) ** 2 for r in ex) / (len(ex) - 1)
        sd = math.sqrt(var)
        return (mean / sd * math.sqrt(252)) if sd > 0 else 0.0

    Engine.calculate_sharpe_ratio = calculate_sharpe_ratio

    def _periods_per_year(self, equity_curve):
        return 252.0

    Engine._periods_per_year = _periods_per_year


def _d7_monte_carlo(_, Metrics, *__):
    """Shuffle the P&L vector and sum it: permutation-invariant, so constant."""
    def generate_monte_carlo_simulation(self, trades, n_simulations=1000, seed=7):
        if not trades:
            return {"simulations": 0, "final_returns": [], "ci_95": (0.0, 0.0),
                    "probability_of_profit": 0.0, "max_drawdown_ci_95": (0.0, 0.0),
                    "median_max_drawdown": 0.0}
        pnl = [float(t.get("pnl", 0.0)) for t in trades]
        results = []
        for _ in range(n_simulations):
            s = pnl[:]
            random.shuffle(s)
            results.append(sum(s))
        ordered = sorted(results)
        lo = max(0, int(len(ordered) * 0.025) - 1)
        hi = min(len(ordered) - 1, int(len(ordered) * 0.975) - 1)
        return {"simulations": n_simulations, "final_returns": results,
                "ci_95": (ordered[lo], ordered[hi]),
                "probability_of_profit": len([v for v in results if v > 0]) / len(results),
                "max_drawdown_ci_95": (0.0, 0.0), "median_max_drawdown": 0.0}
    Metrics.generate_monte_carlo_simulation = generate_monte_carlo_simulation


def _d10_scaler(_, __, FeatureEngineer, ___):
    """Refit the scaler on whatever is passed to transform, one row included."""
    def transform(self, X):
        if self._scaler is None:
            return X
        arr = self._np.asarray(X, dtype=float)
        if arr.ndim == 3:
            n, seq, f = arr.shape
            return self._sklearn_pre.StandardScaler().fit_transform(arr.reshape(-1, f)).reshape(n, seq, f)
        return self._sklearn_pre.StandardScaler().fit_transform(arr)
    FeatureEngineer.transform = transform


def _d11_embargo(_, __, ___, ModelTrainer):
    """Plain 80/20 index cut with no gap between folds."""
    def split_with_embargo(self, X, y, train_frac=0.8, embargo=None):
        split = max(1, int(len(X) * train_frac))
        return X[:split], X[split:], y[:split], y[split:]
    ModelTrainer.split_with_embargo = split_with_embargo


def _d_fee_split(Engine, *_):
    """Split the round-trip fee evenly instead of charging each leg."""
    def _close_position(self, pos, ts, exit_info, trades, fee_rate, slippage):
        qty = pos["quantity"]
        is_long = pos["side"] == "BUY"
        exit_price = exit_info["price"] * (1 - slippage if is_long else 1 + slippage)
        n_in, n_out = pos["entry_price"] * qty, exit_price * qty
        fees = (n_in + n_out) * fee_rate
        gross = (exit_price - pos["entry_price"]) * qty if is_long else (pos["entry_price"] - exit_price) * qty
        trades.append({
            "symbol": pos["symbol"], "side": pos["side"], "entry_time": pos["entry_time"],
            "exit_time": ts, "entry_price": pos["entry_price"], "exit_price": exit_price,
            "quantity": qty, "fee": fees, "pnl": gross - fees,
            "pnl_pct": (gross - fees) / n_in if n_in > 0 else 0.0,
            "duration_seconds": int((ts - pos["entry_time"]).total_seconds()),
            "signal_id": pos.get("signal_id"), "exit_reason": exit_info["reason"],
        })
        return (n_out - fees / 2) if is_long else -(n_out + fees / 2)
    Engine._close_position = _close_position


MUTATIONS = {
    "D2_lookahead_price_lookup": _d2_lookahead,
    "D4_short_mark_to_market": _d4_short_sign,
    "D5_close_only_exits": _d5_close_only_exits,
    "D6_sharpe_annualisation": _d6_sharpe_annualisation,
    "D7_monte_carlo_permutation": _d7_monte_carlo,
    "D10_scaler_refit": _d10_scaler,
    "D11_no_embargo": _d11_embargo,
    "DX_fee_split_evenly": _d_fee_split,
}


def pytest_configure(config):  # noqa: ARG001
    name = os.environ.get("BINFIN_MUTATION")
    if not name:
        return
    if name not in MUTATIONS:
        raise SystemExit(f"unknown mutation {name}; known: {sorted(MUTATIONS)}")
    targets = _import_targets()
    MUTATIONS[name](*targets)
    print(f"\n[mutation-plugin] reintroduced defect: {name}")
