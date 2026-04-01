from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.database import db_manager, execute_raw_sql

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    run_id: int
    metrics: dict[str, Any]
    trades: list[dict[str, Any]]
    equity_curve: list[dict[str, Any]]
    report: dict[str, Any]


class BacktestEngine:
    async def run_backtest(
        self,
        start_date: datetime,
        end_date: datetime,
        coins: list[str],
        strategy_config: dict[str, Any],
    ) -> BacktestResult:
        signals = await self._load_signals(start_date, end_date, coins)
        prices = await self._load_prices(start_date, end_date, coins)
        trades, equity_curve = self.simulate_trades(
            signals=signals,
            prices=prices,
            initial_capital=float(strategy_config.get("initial_capital", 10_000.0)),
            strategy_config=strategy_config,
        )

        metrics = self.calculate_performance_metrics(trades, equity_curve)
        report = self.generate_backtest_report(
            {
                "start_date": start_date,
                "end_date": end_date,
                "coins": coins,
                "trades": trades,
                "equity_curve": equity_curve,
                "metrics": metrics,
            }
        )
        run_id = await self.save_backtest_results(0, {
            "coins": coins,
            "strategy_config": strategy_config,
            "metrics": metrics,
            "trades": trades,
            "equity_curve": equity_curve,
        })

        return BacktestResult(
            run_id=run_id,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            report=report,
        )

    def simulate_trades(
        self,
        signals: list[dict[str, Any]],
        prices: list[dict[str, Any]],
        initial_capital: float = 10_000.0,
        strategy_config: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        strategy_config = strategy_config or {}
        transaction_cost = float(strategy_config.get("transaction_cost", 0.001))
        slippage = float(strategy_config.get("slippage", 0.0005))
        max_position_pct = float(strategy_config.get("max_position_pct", 0.2))
        timeout_hours = int(strategy_config.get("timeout_hours", 24))

        price_index = {(row["symbol"], row["ts"]): row for row in prices}
        price_by_symbol: dict[str, list[dict[str, Any]]] = {}
        for row in prices:
            price_by_symbol.setdefault(row["symbol"], []).append(row)
        for symbol in price_by_symbol:
            price_by_symbol[symbol].sort(key=lambda item: item["ts"])

        cash = initial_capital
        open_positions: list[dict[str, Any]] = []
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []

        for signal in sorted(signals, key=lambda row: row["ts"]):
            ts = signal["ts"]
            symbol = signal["symbol"]
            signal_type = signal["signal"]
            strength = float(signal.get("strength", 0.0))

            current_price = self._nearest_price(symbol, ts, price_by_symbol)
            if current_price <= 0:
                continue

            # Process open positions first.
            remaining_positions: list[dict[str, Any]] = []
            for pos in open_positions:
                exit_info = self._check_exit_conditions(pos, ts, current_price, timeout_hours)
                if exit_info is None:
                    remaining_positions.append(pos)
                    continue

                qty = pos["quantity"]
                exit_price = exit_info["price"] * (1 - slippage if pos["side"] == "BUY" else 1 + slippage)
                gross_pnl = (exit_price - pos["entry_price"]) * qty if pos["side"] == "BUY" else (pos["entry_price"] - exit_price) * qty
                fees = (pos["entry_price"] * qty + exit_price * qty) * transaction_cost
                net_pnl = gross_pnl - fees

                cash += (pos["entry_price"] * qty) + net_pnl
                trades.append(
                    {
                        "symbol": pos["symbol"],
                        "side": pos["side"],
                        "entry_time": pos["entry_time"],
                        "exit_time": ts,
                        "entry_price": pos["entry_price"],
                        "exit_price": exit_price,
                        "quantity": qty,
                        "fee": fees,
                        "pnl": net_pnl,
                        "pnl_pct": net_pnl / (pos["entry_price"] * qty) if pos["entry_price"] > 0 else 0,
                        "duration_seconds": int((ts - pos["entry_time"]).total_seconds()),
                        "signal_id": pos.get("signal_id"),
                        "exit_reason": exit_info["reason"],
                    }
                )

            open_positions = remaining_positions

            # Execute new position when signal indicates and capital exists.
            if signal_type in {"BUY", "SELL"} and cash > 0:
                allocation = cash * max_position_pct * min(1.0, max(0.1, strength / 10.0))
                if allocation > 0:
                    quantity = allocation / (current_price * (1 + slippage))
                    entry_price = current_price * (1 + slippage if signal_type == "BUY" else 1 - slippage)
                    open_positions.append(
                        {
                            "symbol": symbol,
                            "side": signal_type,
                            "entry_time": ts,
                            "entry_price": entry_price,
                            "quantity": quantity,
                            "target": float(signal.get("take_profit", current_price * (1.02 if signal_type == "BUY" else 0.98))),
                            "stop": float(signal.get("stop_loss", current_price * (0.985 if signal_type == "BUY" else 1.015))),
                            "signal_id": signal.get("id"),
                        }
                    )
                    cash -= allocation

            mark_to_market = sum(
                (self._nearest_price(pos["symbol"], ts, price_by_symbol) * pos["quantity"]) for pos in open_positions
            )
            equity_curve.append({"ts": ts, "portfolio_value": cash + mark_to_market, "cash": cash, "open_positions": len(open_positions)})

        return trades, equity_curve

    def calculate_performance_metrics(self, trades: list[dict[str, Any]], portfolio_values: list[dict[str, Any]]) -> dict[str, Any]:
        total_trades = len(trades)
        winners = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
        losers = [t for t in trades if float(t.get("pnl", 0.0)) <= 0]
        win_rate = (len(winners) / total_trades * 100) if total_trades else 0.0

        avg_return = sum(float(t.get("pnl_pct", 0.0)) for t in trades) / total_trades if total_trades else 0.0
        initial = portfolio_values[0]["portfolio_value"] if portfolio_values else 0.0
        final = portfolio_values[-1]["portfolio_value"] if portfolio_values else 0.0
        total_return = ((final - initial) / initial * 100) if initial else 0.0

        returns = [float(t.get("pnl_pct", 0.0)) for t in trades]
        sharpe = self.calculate_sharpe_ratio(returns)
        drawdown = self.calculate_max_drawdown(portfolio_values)

        gross_profit = sum(float(t.get("pnl", 0.0)) for t in winners)
        gross_loss = abs(sum(float(t.get("pnl", 0.0)) for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit

        avg_hold = (
            sum(float(t.get("duration_seconds", 0.0)) for t in trades) / total_trades
            if total_trades
            else 0.0
        )
        best_trade = max((float(t.get("pnl", 0.0)) for t in trades), default=0.0)
        worst_trade = min((float(t.get("pnl", 0.0)) for t in trades), default=0.0)

        return {
            "total_trades": total_trades,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate_pct": round(win_rate, 4),
            "avg_return_per_trade": round(avg_return, 6),
            "total_return_pct": round(total_return, 4),
            "sharpe_ratio": round(sharpe, 6),
            "max_drawdown_pct": round(drawdown["max_drawdown_pct"], 4),
            "max_drawdown_duration": drawdown["duration"],
            "profit_factor": round(float(profit_factor), 6),
            "avg_holding_time_seconds": round(avg_hold, 2),
            "best_trade": round(best_trade, 6),
            "worst_trade": round(worst_trade, 6),
        }

    def calculate_sharpe_ratio(self, returns: list[float], risk_free_rate: float = 0.02) -> float:
        if not returns:
            return 0.0
        import math

        rf_daily = risk_free_rate / 252
        excess = [r - rf_daily for r in returns]
        mean = sum(excess) / len(excess)
        variance = sum((r - mean) ** 2 for r in excess) / max(len(excess) - 1, 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    def calculate_max_drawdown(self, portfolio_values: list[dict[str, Any]]) -> dict[str, Any]:
        if not portfolio_values:
            return {"max_drawdown_pct": 0.0, "duration": 0}

        peak = portfolio_values[0]["portfolio_value"]
        peak_ts = portfolio_values[0]["ts"]
        max_dd = 0.0
        max_duration = 0

        for row in portfolio_values:
            value = float(row["portfolio_value"])
            ts = row["ts"]
            if value > peak:
                peak = value
                peak_ts = ts
            dd = (peak - value) / peak * 100 if peak > 0 else 0.0
            duration = int((ts - peak_ts).total_seconds()) if isinstance(ts, datetime) and isinstance(peak_ts, datetime) else 0
            if dd > max_dd:
                max_dd = dd
                max_duration = duration

        return {"max_drawdown_pct": max_dd, "duration": max_duration}

    async def save_backtest_results(self, run_id: int, results: dict[str, Any]) -> int:
        async with db_manager.session_factory() as session:
            if run_id <= 0:
                row = (
                    await execute_raw_sql(
                        session,
                        "INSERT INTO backtest_runs (strategy_name, symbol, interval, started_at, ended_at, "
                        "initial_capital, final_capital, pnl, pnl_pct, sharpe_ratio, max_drawdown, win_rate, "
                        "trade_count, config, metrics, created_at) "
                        "VALUES (:strategy, :symbol, :interval, NOW(), NOW(), :initial, :final, :pnl, :pnl_pct, "
                        ":sharpe, :max_dd, :win_rate, :trade_count, CAST(:config AS jsonb), CAST(:metrics AS jsonb), NOW()) "
                        "RETURNING id",
                        {
                            "strategy": results.get("strategy_config", {}).get("name", "signal_strategy"),
                            "symbol": ",".join(results.get("coins", [])) or "MULTI",
                            "interval": "15m",
                            "initial": float(results.get("strategy_config", {}).get("initial_capital", 10_000.0)),
                            "final": float(results.get("equity_curve", [{}])[-1].get("portfolio_value", 0.0)) if results.get("equity_curve") else 0.0,
                            "pnl": float(results.get("metrics", {}).get("total_return_pct", 0.0)),
                            "pnl_pct": float(results.get("metrics", {}).get("total_return_pct", 0.0)),
                            "sharpe": float(results.get("metrics", {}).get("sharpe_ratio", 0.0)),
                            "max_dd": float(results.get("metrics", {}).get("max_drawdown_pct", 0.0)),
                            "win_rate": float(results.get("metrics", {}).get("win_rate_pct", 0.0)),
                            "trade_count": int(results.get("metrics", {}).get("total_trades", 0)),
                            "config": json.dumps(results.get("strategy_config", {})),
                            "metrics": json.dumps(results.get("metrics", {})),
                        },
                    )
                ).first()
                run_id = int(row.id) if row else 0

            for trade in results.get("trades", []):
                await execute_raw_sql(
                    session,
                    "INSERT INTO backtest_trades (run_id, symbol, side, quantity, entry_time, exit_time, entry_price, "
                    "exit_price, fee, pnl, pnl_pct, duration_seconds, signal_id, metadata, created_at) "
                    "VALUES (:run_id, :symbol, :side, :quantity, :entry_time, :exit_time, :entry_price, :exit_price, "
                    ":fee, :pnl, :pnl_pct, :duration_seconds, :signal_id, CAST(:metadata AS jsonb), NOW())",
                    {
                        "run_id": run_id,
                        "symbol": trade.get("symbol"),
                        "side": trade.get("side"),
                        "quantity": trade.get("quantity"),
                        "entry_time": trade.get("entry_time"),
                        "exit_time": trade.get("exit_time"),
                        "entry_price": trade.get("entry_price"),
                        "exit_price": trade.get("exit_price"),
                        "fee": trade.get("fee", 0.0),
                        "pnl": trade.get("pnl", 0.0),
                        "pnl_pct": trade.get("pnl_pct", 0.0),
                        "duration_seconds": trade.get("duration_seconds", 0),
                        "signal_id": trade.get("signal_id"),
                        "metadata": json.dumps({"exit_reason": trade.get("exit_reason")}),
                    },
                )

            await db_manager.redis_client.set(
                f"backtest:equity_curve:{run_id}",
                json.dumps(results.get("equity_curve", []), default=str),
                ex=24 * 3600,
            )

            await session.commit()
        return run_id

    def generate_backtest_report(self, results: dict[str, Any]) -> dict[str, Any]:
        trades = results.get("trades", [])
        metrics = results.get("metrics", {})
        equity = results.get("equity_curve", [])
        wins = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
        losses = [t for t in trades if float(t.get("pnl", 0.0)) <= 0]

        return {
            "summary": {
                "period": {
                    "start": str(results.get("start_date")),
                    "end": str(results.get("end_date")),
                },
                "coins": results.get("coins", []),
                "metrics": metrics,
            },
            "trade_list": trades,
            "equity_curve": equity,
            "drawdown_chart": {
                "max_drawdown_pct": metrics.get("max_drawdown_pct", 0.0),
                "duration": metrics.get("max_drawdown_duration", 0),
            },
            "win_loss_distribution": {
                "wins": len(wins),
                "losses": len(losses),
            },
        }

    def compare_strategies(self, results_list: list[dict[str, Any]]) -> dict[str, Any]:
        rows = []
        for result in results_list:
            metrics = result.get("metrics", {})
            rows.append(
                {
                    "strategy": result.get("strategy", "unknown"),
                    "sharpe_ratio": float(metrics.get("sharpe_ratio", 0.0)),
                    "total_return_pct": float(metrics.get("total_return_pct", 0.0)),
                    "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0.0)),
                }
            )
        ranked = sorted(rows, key=lambda r: (r["sharpe_ratio"], r["total_return_pct"]), reverse=True)
        return {"ranked": ranked}

    async def _load_signals(self, start_date: datetime, end_date: datetime, coins: list[str]) -> list[dict[str, Any]]:
        async with db_manager.session_factory() as session:
            rows = (
                await execute_raw_sql(
                    session,
                    "SELECT id, ts, symbol, signal, strength, confidence, entry_price, stop_loss, take_profit "
                    "FROM trading_signals WHERE ts BETWEEN :start AND :end AND symbol = ANY(:coins) "
                    "ORDER BY ts ASC",
                    {"start": start_date, "end": end_date, "coins": [coin.upper() for coin in coins]},
                )
            ).all()
        return [dict(row._mapping) for row in rows]

    async def _load_prices(self, start_date: datetime, end_date: datetime, coins: list[str]) -> list[dict[str, Any]]:
        symbols = [f"{coin.upper()}USDT" for coin in coins]
        async with db_manager.session_factory() as session:
            rows = (
                await execute_raw_sql(
                    session,
                    "SELECT ts, symbol, close FROM price_data "
                    "WHERE ts BETWEEN :start AND :end AND symbol = ANY(:symbols) AND interval = '15m' "
                    "ORDER BY ts ASC",
                    {"start": start_date, "end": end_date, "symbols": symbols},
                )
            ).all()
        mapped: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row._mapping)
            item["symbol"] = str(item["symbol"]).replace("USDT", "")
            mapped.append(item)
        return mapped

    def _nearest_price(self, symbol: str, ts: datetime, prices: dict[str, list[dict[str, Any]]]) -> float:
        rows = prices.get(symbol, [])
        if not rows:
            return 0.0
        nearest = min(rows, key=lambda row: abs((row["ts"] - ts).total_seconds()))
        return float(nearest.get("close", 0.0) or 0.0)

    def _check_exit_conditions(
        self,
        position: dict[str, Any],
        ts: datetime,
        current_price: float,
        timeout_hours: int,
    ) -> dict[str, Any] | None:
        side = position["side"]
        target = float(position["target"])
        stop = float(position["stop"])
        age = ts - position["entry_time"]

        if side == "BUY":
            if current_price >= target:
                return {"reason": "target", "price": current_price}
            if current_price <= stop:
                return {"reason": "stop", "price": current_price}
        if side == "SELL":
            if current_price <= target:
                return {"reason": "target", "price": current_price}
            if current_price >= stop:
                return {"reason": "stop", "price": current_price}

        if age >= timedelta(hours=timeout_hours):
            return {"reason": "timeout", "price": current_price}
        return None


async def run_backtest_async(strategy_name: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    engine = BacktestEngine()
    result = await engine.run_backtest(
        start_date=now - timedelta(days=30),
        end_date=now,
        coins=["BTC", "ETH"],
        strategy_config={"name": strategy_name, "initial_capital": 10_000.0},
    )
    return {
        "run_id": result.run_id,
        "metrics": result.metrics,
    }


def run_backtest(strategy_name: str) -> dict[str, Any]:
    return asyncio.run(run_backtest_async(strategy_name))
