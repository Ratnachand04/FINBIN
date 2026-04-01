from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Any


class PerformanceMetrics:
    def calculate_risk_metrics(self, returns: list[float]) -> dict[str, float]:
        if not returns:
            return {
                "volatility_annualized": 0.0,
                "semi_deviation": 0.0,
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
                "max_consecutive_losses": 0.0,
            }

        volatility_daily = self._std(returns)
        volatility_annualized = volatility_daily * math.sqrt(252)
        downside = [r for r in returns if r < 0]
        semi_deviation = self._std(downside)

        sorted_returns = sorted(returns)
        idx_95 = max(0, int(len(sorted_returns) * 0.05) - 1)
        idx_99 = max(0, int(len(sorted_returns) * 0.01) - 1)
        var_95 = abs(sorted_returns[idx_95])
        var_99 = abs(sorted_returns[idx_99])
        cvar_95_tail = sorted_returns[: max(1, int(len(sorted_returns) * 0.05))]
        cvar_95 = abs(mean(cvar_95_tail))

        max_losses = self._max_consecutive(returns, lambda x: x < 0)

        return {
            "volatility_annualized": float(volatility_annualized),
            "semi_deviation": float(semi_deviation),
            "var_95": float(var_95),
            "var_99": float(var_99),
            "cvar_95": float(cvar_95),
            "max_consecutive_losses": float(max_losses),
        }

    def calculate_return_metrics(self, trades: list[dict[str, Any]], initial_capital: float) -> dict[str, Any]:
        if not trades or initial_capital <= 0:
            return {
                "total_return_pct": 0.0,
                "annualized_return": 0.0,
                "cagr": 0.0,
                "monthly_returns": {},
                "best_month": None,
                "worst_month": None,
                "return_consistency": 0.0,
            }

        pnl_sum = sum(float(t.get("pnl", 0.0)) for t in trades)
        total_return = pnl_sum / initial_capital

        dates = [self._coerce_dt(t.get("exit_time") or t.get("entry_time")) for t in trades]
        start, end = min(dates), max(dates)
        years = max((end - start).days / 365.25, 1 / 365.25)

        annualized_return = ((1 + total_return) ** (1 / years)) - 1 if (1 + total_return) > 0 else -1.0
        cagr = annualized_return

        monthly = defaultdict(float)
        for trade in trades:
            ts = self._coerce_dt(trade.get("exit_time") or trade.get("entry_time"))
            key = ts.strftime("%Y-%m")
            monthly[key] += float(trade.get("pnl", 0.0)) / initial_capital

        monthly_returns = {k: v * 100 for k, v in sorted(monthly.items())}
        if monthly_returns:
            best_month = max(monthly_returns, key=monthly_returns.get)
            worst_month = min(monthly_returns, key=monthly_returns.get)
            consistency = self._std(list(monthly_returns.values()))
        else:
            best_month, worst_month, consistency = None, None, 0.0

        return {
            "total_return_pct": total_return * 100,
            "annualized_return": annualized_return * 100,
            "cagr": cagr * 100,
            "monthly_returns": monthly_returns,
            "best_month": {"month": best_month, "return_pct": monthly_returns.get(best_month)} if best_month else None,
            "worst_month": {"month": worst_month, "return_pct": monthly_returns.get(worst_month)} if worst_month else None,
            "return_consistency": float(consistency),
        }

    def calculate_trade_metrics(self, trades: list[dict[str, Any]]) -> dict[str, float]:
        if not trades:
            return {
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "avg_trade_duration_seconds": 0.0,
                "trades_per_month": 0.0,
                "max_consecutive_wins": 0.0,
                "max_consecutive_losses": 0.0,
            }

        pnls = [float(t.get("pnl", 0.0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(trades)

        durations = [float(t.get("duration_seconds", 0.0)) for t in trades]
        dates = [self._coerce_dt(t.get("exit_time") or t.get("entry_time")) for t in trades]
        months = max(1, len({d.strftime("%Y-%m") for d in dates}))

        returns = [float(t.get("pnl_pct", 0.0)) for t in trades]
        max_wins = self._max_consecutive(returns, lambda x: x > 0)
        max_losses = self._max_consecutive(returns, lambda x: x <= 0)

        return {
            "win_rate": win_rate,
            "avg_win": mean(wins) if wins else 0.0,
            "avg_loss": mean(losses) if losses else 0.0,
            "largest_win": max(wins) if wins else 0.0,
            "largest_loss": min(losses) if losses else 0.0,
            "avg_trade_duration_seconds": mean(durations) if durations else 0.0,
            "trades_per_month": len(trades) / months,
            "max_consecutive_wins": float(max_wins),
            "max_consecutive_losses": float(max_losses),
        }

    def calculate_risk_adjusted_returns(self, returns: list[float], trades: list[dict[str, Any]]) -> dict[str, float]:
        if not returns:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "omega_ratio": 0.0,
            }

        rf_daily = 0.02 / 252
        excess = [r - rf_daily for r in returns]
        sharpe = (mean(excess) / self._std(excess) * math.sqrt(252)) if self._std(excess) > 0 else 0.0

        downside = [r for r in excess if r < 0]
        downside_dev = self._std(downside)
        sortino = (mean(excess) / downside_dev * math.sqrt(252)) if downside_dev > 0 else 0.0

        total_return = sum(returns)
        drawdown_pct = abs(min([0.0] + [float(t.get("pnl_pct", 0.0)) for t in trades]))
        calmar = (total_return / drawdown_pct) if drawdown_pct > 0 else 0.0

        threshold = 0.0
        gains = sum(max(r - threshold, 0) for r in returns)
        losses = abs(sum(min(r - threshold, 0) for r in returns))
        omega = gains / losses if losses > 0 else gains

        return {
            "sharpe_ratio": float(sharpe),
            "sortino_ratio": float(sortino),
            "calmar_ratio": float(calmar),
            "omega_ratio": float(omega),
        }

    def calculate_drawdown_metrics(self, equity_curve: list[dict[str, Any]]) -> dict[str, float]:
        if not equity_curve:
            return {
                "max_drawdown_pct": 0.0,
                "avg_drawdown_pct": 0.0,
                "drawdown_duration_days": 0.0,
                "recovery_time_days": 0.0,
                "drawdown_periods": 0.0,
            }

        peaks = []
        running_peak = float(equity_curve[0]["portfolio_value"])
        drawdowns = []
        periods = 0
        in_drawdown = False
        dd_start: datetime | None = None
        recovery_durations = []

        for point in equity_curve:
            value = float(point["portfolio_value"])
            ts = self._coerce_dt(point.get("ts"))
            running_peak = max(running_peak, value)
            peaks.append(running_peak)
            dd = (running_peak - value) / running_peak * 100 if running_peak > 0 else 0.0
            drawdowns.append(dd)

            if dd > 0 and not in_drawdown:
                in_drawdown = True
                periods += 1
                dd_start = ts
            if dd == 0 and in_drawdown:
                in_drawdown = False
                if dd_start:
                    recovery_durations.append((ts - dd_start).days)
                dd_start = None

        non_zero_dd = [d for d in drawdowns if d > 0]
        max_dd = max(drawdowns) if drawdowns else 0.0
        avg_dd = mean(non_zero_dd) if non_zero_dd else 0.0
        recovery = mean(recovery_durations) if recovery_durations else 0.0

        # Approximate drawdown duration as median recovery period.
        duration = median(recovery_durations) if recovery_durations else 0.0

        return {
            "max_drawdown_pct": float(max_dd),
            "avg_drawdown_pct": float(avg_dd),
            "drawdown_duration_days": float(duration),
            "recovery_time_days": float(recovery),
            "drawdown_periods": float(periods),
        }

    def calculate_expectancy(self, trades: list[dict[str, Any]]) -> float:
        if not trades:
            return 0.0
        pnls = [float(t.get("pnl", 0.0)) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls)
        loss_rate = len(losses) / len(pnls)
        avg_win = mean(wins) if wins else 0.0
        avg_loss = abs(mean(losses)) if losses else 0.0
        return (win_rate * avg_win) - (loss_rate * avg_loss)

    def generate_monte_carlo_simulation(self, trades: list[dict[str, Any]], n_simulations: int = 1000) -> dict[str, Any]:
        if not trades:
            return {
                "simulations": 0,
                "final_returns": [],
                "ci_95": (0.0, 0.0),
                "probability_of_profit": 0.0,
            }

        pnl_series = [float(t.get("pnl", 0.0)) for t in trades]
        results = []
        for _ in range(n_simulations):
            shuffled = pnl_series[:]
            random.shuffle(shuffled)
            results.append(sum(shuffled))

        ordered = sorted(results)
        low_idx = max(0, int(len(ordered) * 0.025) - 1)
        high_idx = min(len(ordered) - 1, int(len(ordered) * 0.975) - 1)
        ci_95 = (ordered[low_idx], ordered[high_idx])
        profit_prob = len([v for v in results if v > 0]) / len(results)

        return {
            "simulations": n_simulations,
            "final_returns": results,
            "ci_95": ci_95,
            "probability_of_profit": profit_prob,
        }

    def compare_to_benchmark(self, strategy_returns: list[float], benchmark_returns: list[float]) -> dict[str, float]:
        if not strategy_returns or not benchmark_returns:
            return {
                "alpha": 0.0,
                "beta": 0.0,
                "information_ratio": 0.0,
                "tracking_error": 0.0,
            }

        n = min(len(strategy_returns), len(benchmark_returns))
        s = strategy_returns[:n]
        b = benchmark_returns[:n]
        excess = [sv - bv for sv, bv in zip(s, b)]

        b_var = self._variance(b)
        cov = self._covariance(s, b)
        beta = cov / b_var if b_var > 0 else 0.0
        alpha = mean(s) - beta * mean(b)
        tracking_error = self._std(excess) * math.sqrt(252)
        info_ratio = (mean(excess) / self._std(excess) * math.sqrt(252)) if self._std(excess) > 0 else 0.0

        return {
            "alpha": float(alpha),
            "beta": float(beta),
            "information_ratio": float(info_ratio),
            "tracking_error": float(tracking_error),
        }

    def confidence_interval_mean(self, values: list[float], confidence: float = 0.95) -> tuple[float, float]:
        if not values:
            return (0.0, 0.0)
        z = 1.96 if confidence >= 0.95 else 1.64
        m = mean(values)
        se = self._std(values) / math.sqrt(max(1, len(values)))
        return (m - z * se, m + z * se)

    def t_test_zero_mean(self, values: list[float]) -> dict[str, float]:
        if len(values) < 2:
            return {"t_stat": 0.0, "n": float(len(values))}
        m = mean(values)
        s = self._std(values)
        if s == 0:
            return {"t_stat": 0.0, "n": float(len(values))}
        t_stat = m / (s / math.sqrt(len(values)))
        return {"t_stat": float(t_stat), "n": float(len(values))}

    def prepare_visualization_payload(self, trades: list[dict[str, Any]], equity_curve: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "equity_curve": [
                {"ts": str(row.get("ts")), "portfolio_value": float(row.get("portfolio_value", 0.0))}
                for row in equity_curve
            ],
            "trade_pnl_distribution": [float(t.get("pnl", 0.0)) for t in trades],
            "drawdown_curve": self._drawdown_curve(equity_curve),
        }

    def _drawdown_curve(self, equity_curve: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not equity_curve:
            return []
        peak = float(equity_curve[0].get("portfolio_value", 0.0))
        out = []
        for row in equity_curve:
            value = float(row.get("portfolio_value", 0.0))
            peak = max(peak, value)
            dd = (peak - value) / peak * 100 if peak > 0 else 0.0
            out.append({"ts": str(row.get("ts")), "drawdown_pct": dd})
        return out

    def _coerce_dt(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return datetime.utcnow()

    def _std(self, values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = mean(values)
        return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))

    def _variance(self, values: list[float]) -> float:
        s = self._std(values)
        return s * s

    def _covariance(self, x: list[float], y: list[float]) -> float:
        if len(x) < 2 or len(y) < 2:
            return 0.0
        n = min(len(x), len(y))
        x, y = x[:n], y[:n]
        mx, my = mean(x), mean(y)
        return sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)

    def _max_consecutive(self, values: list[float], predicate: Any) -> int:
        streak = 0
        best = 0
        for value in values:
            if predicate(value):
                streak += 1
                best = max(best, streak)
            else:
                streak = 0
        return best
