from __future__ import annotations

import asyncio
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from backend.database import db_manager, execute_raw_sql

logger = logging.getLogger(__name__)

# Fallback bar cadence (15m) used only when the equity curve is too short to
# infer one. Annualisation factors are derived from the actual bar spacing.
DEFAULT_BAR_SECONDS = 900
SECONDS_PER_YEAR = 365 * 24 * 3600


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
        # Binance spot taker fee is 4bp per side; the previous 10bp default was
        # applied to notional *twice* which double-counted the round trip.
        fee_rate = float(strategy_config.get("transaction_cost", 0.0004))
        slippage = float(strategy_config.get("slippage", 0.0005))
        max_position_pct = float(strategy_config.get("max_position_pct", 0.2))
        timeout_hours = int(strategy_config.get("timeout_hours", 24))
        max_concurrent = int(strategy_config.get("max_concurrent_positions", 5))
        allow_short = bool(strategy_config.get("allow_short", True))
        # Holding period in *bars*, counted from the entry bar. hold_bars=1
        # closes at the close of the bar the position was opened on, so a
        # position entered at the open of bar k realises O_k -> C_k. Leaving
        # this unset falls back to the wall-clock timeout, which on daily bars
        # cannot close before the following bar and therefore spans two days --
        # a mismatch against a one-day-ahead label.
        hold_bars = strategy_config.get("hold_bars")
        hold_bars = int(hold_bars) if hold_bars is not None else None

        bars_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in prices:
            bars_by_symbol[row["symbol"]].append(row)
        for symbol in bars_by_symbol:
            bars_by_symbol[symbol].sort(key=lambda item: item["ts"])

        # A signal computed from the close of bar i can only be acted on at bar
        # i+1. Scheduling entries here (rather than filling at the signal's own
        # bar) is what removes the look-ahead from the fill path.
        scheduled: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
        for signal in signals:
            symbol = signal.get("symbol")
            rows = bars_by_symbol.get(symbol)
            if not rows:
                continue
            idx = self._bar_index_at_or_before(rows, signal["ts"])
            if idx is None or idx + 1 >= len(rows):
                continue
            scheduled[(symbol, idx + 1)].append(signal)

        cash = float(initial_capital)
        positions: dict[str, dict[str, Any]] = {}
        trades: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []

        timeline = sorted({row["ts"] for row in prices})
        cursor = {symbol: 0 for symbol in bars_by_symbol}

        for ts in timeline:
            # Advance each symbol to its bar at this timestamp, if it has one.
            bars_now: dict[str, dict[str, Any]] = {}
            for symbol, rows in bars_by_symbol.items():
                i = cursor[symbol]
                if i < len(rows) and rows[i]["ts"] == ts:
                    bars_now[symbol] = rows[i]
                    cursor[symbol] = i + 1

            # 1. Exits, each position priced from its OWN symbol's bar.
            for symbol, bar in bars_now.items():
                pos = positions.get(symbol)
                if pos is None:
                    continue
                exit_info = self._check_exit_conditions(pos, ts, bar, timeout_hours)
                if exit_info is None:
                    continue
                cash += self._close_position(
                    pos, ts, exit_info, trades, fee_rate, slippage
                )
                del positions[symbol]

            # 2. Entries scheduled for this bar, filled at the bar's open.
            for symbol, bar in bars_now.items():
                idx = cursor[symbol] - 1
                for signal in scheduled.get((symbol, idx), []):
                    signal_type = signal.get("signal")
                    if signal_type not in {"BUY", "SELL"}:
                        continue
                    if signal_type == "SELL" and not allow_short:
                        continue
                    if symbol in positions or len(positions) >= max_concurrent:
                        continue

                    fill = float(bar.get("open") or bar.get("close") or 0.0)
                    if fill <= 0:
                        continue

                    equity_now = self._equity(cash, positions, bars_now, bars_by_symbol, cursor)
                    strength = float(signal.get("strength", 0.0) or 0.0)
                    size_mult = min(1.0, max(0.1, strength / 10.0))
                    allocation = equity_now * max_position_pct * size_mult
                    is_long = signal_type == "BUY"
                    if is_long:
                        allocation = min(allocation, cash)
                    if allocation <= 0:
                        continue

                    entry_price = fill * (1 + slippage if is_long else 1 - slippage)
                    quantity = allocation / entry_price
                    entry_fee = allocation * fee_rate
                    # Long consumes cash; short credits proceeds. The offsetting
                    # short liability is carried in the equity calculation.
                    cash += (-allocation - entry_fee) if is_long else (allocation - entry_fee)

                    positions[symbol] = {
                        "symbol": symbol,
                        "side": signal_type,
                        "entry_time": ts,
                        "entry_bar": idx,
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "target": self._level(signal, "take_profit", fill, 1.02 if is_long else 0.98),
                        "stop": self._level(signal, "stop_loss", fill, 0.985 if is_long else 1.015),
                        "signal_id": signal.get("id"),
                    }

            # 3. Bar-counted holding period expires at this bar's close. Run
            #    after entries so a one-bar hold can open and close on the same
            #    bar, matching a one-period-ahead label.
            if hold_bars is not None:
                for symbol, bar in bars_now.items():
                    pos = positions.get(symbol)
                    if pos is None:
                        continue
                    if (cursor[symbol] - 1) - pos["entry_bar"] < hold_bars - 1:
                        continue
                    close = float(bar.get("close") or 0.0)
                    if close <= 0:
                        continue
                    cash += self._close_position(
                        pos, ts, {"reason": "hold_expiry", "price": close},
                        trades, fee_rate, slippage,
                    )
                    del positions[symbol]

            equity = self._equity(cash, positions, bars_now, bars_by_symbol, cursor)
            equity_curve.append(
                {
                    "ts": ts,
                    "portfolio_value": equity,
                    "cash": cash,
                    "open_positions": len(positions),
                }
            )

        return trades, equity_curve

    def _close_position(
        self,
        pos: dict[str, Any],
        ts: datetime,
        exit_info: dict[str, Any],
        trades: list[dict[str, Any]],
        fee_rate: float,
        slippage: float,
    ) -> float:
        """Book a closing trade and return the resulting change in cash."""
        qty = pos["quantity"]
        is_long = pos["side"] == "BUY"
        exit_price = exit_info["price"] * (1 - slippage if is_long else 1 + slippage)
        notional_in = pos["entry_price"] * qty
        notional_out = exit_price * qty
        # Each leg is charged on its own notional. Splitting the round trip
        # evenly would make the cash charged diverge from the fee reported on
        # the trade whenever the price moved.
        exit_fee = notional_out * fee_rate
        fees = notional_in * fee_rate + exit_fee
        gross_pnl = (exit_price - pos["entry_price"]) * qty if is_long else (pos["entry_price"] - exit_price) * qty
        net_pnl = gross_pnl - fees

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
                "pnl_pct": net_pnl / notional_in if notional_in > 0 else 0.0,
                "duration_seconds": int((ts - pos["entry_time"]).total_seconds()),
                "signal_id": pos.get("signal_id"),
                "exit_reason": exit_info["reason"],
            }
        )
        # Long: sell back into cash. Short: buy back, paying out cash.
        return (notional_out - exit_fee) if is_long else -(notional_out + exit_fee)

    def _equity(
        self,
        cash: float,
        positions: dict[str, dict[str, Any]],
        bars_now: dict[str, dict[str, Any]],
        bars_by_symbol: dict[str, list[dict[str, Any]]],
        cursor: dict[str, int],
    ) -> float:
        """Cash plus long market value minus short liability.

        Shorts must be subtracted: the previous implementation added
        ``price * quantity`` for every position, so a short gained equity as the
        price rose.
        """
        total = cash
        for symbol, pos in positions.items():
            bar = bars_now.get(symbol)
            if bar is None:
                rows = bars_by_symbol.get(symbol, [])
                i = cursor.get(symbol, 0) - 1
                bar = rows[i] if 0 <= i < len(rows) else None
            mark = float(bar.get("close") or 0.0) if bar else pos["entry_price"]
            value = mark * pos["quantity"]
            total += value if pos["side"] == "BUY" else -value
        return total

    def _level(self, signal: dict[str, Any], key: str, reference: float, default_mult: float) -> float:
        value = signal.get(key)
        try:
            level = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            level = 0.0
        return level if level > 0 else reference * default_mult

    def _bar_index_at_or_before(self, rows: list[dict[str, Any]], ts: datetime) -> int | None:
        """Index of the last bar with ``bar.ts <= ts``.

        The previous ``_nearest_price`` used absolute time distance, which could
        select a bar *after* the signal and leak future prices into the fill.
        """
        lo, hi, found = 0, len(rows) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if rows[mid]["ts"] <= ts:
                found = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return found

    def calculate_performance_metrics(self, trades: list[dict[str, Any]], portfolio_values: list[dict[str, Any]]) -> dict[str, Any]:
        total_trades = len(trades)
        winners = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
        losers = [t for t in trades if float(t.get("pnl", 0.0)) <= 0]
        win_rate = (len(winners) / total_trades * 100) if total_trades else 0.0

        avg_return = sum(float(t.get("pnl_pct", 0.0)) for t in trades) / total_trades if total_trades else 0.0
        initial = portfolio_values[0]["portfolio_value"] if portfolio_values else 0.0
        final = portfolio_values[-1]["portfolio_value"] if portfolio_values else 0.0
        total_return = ((final - initial) / initial * 100) if initial else 0.0

        # Sharpe must come from the equity curve sampled at a fixed cadence.
        # Feeding per-trade returns into a sqrt(252) factor (the previous
        # behaviour) annualises by an arbitrary number and is what produced the
        # double-digit Sharpe values in earlier reports.
        bar_returns = self._equity_returns(portfolio_values)
        periods_per_year = self._periods_per_year(portfolio_values)
        sharpe = self.calculate_sharpe_ratio(bar_returns, periods_per_year=periods_per_year)
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

    def calculate_sharpe_ratio(
        self,
        returns: list[float],
        risk_free_rate: float = 0.02,
        periods_per_year: float = 252.0,
    ) -> float:
        """Annualised Sharpe of a periodic return series.

        ``returns`` must be sampled at a constant cadence and ``periods_per_year``
        must match that cadence. Passing per-trade returns here is a category
        error: trades do not arrive on a fixed clock.
        """
        if len(returns) < 2 or periods_per_year <= 0:
            return 0.0

        rf_per_period = risk_free_rate / periods_per_year
        excess = [r - rf_per_period for r in returns]
        mean = sum(excess) / len(excess)
        variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(periods_per_year)

    def _equity_returns(self, equity_curve: list[dict[str, Any]]) -> list[float]:
        returns: list[float] = []
        for prev, curr in zip(equity_curve, equity_curve[1:]):
            base = float(prev.get("portfolio_value", 0.0))
            if base <= 0:
                continue
            returns.append(float(curr.get("portfolio_value", 0.0)) / base - 1.0)
        return returns

    def _periods_per_year(self, equity_curve: list[dict[str, Any]]) -> float:
        """Infer the annualisation factor from the observed bar spacing."""
        deltas = [
            (curr["ts"] - prev["ts"]).total_seconds()
            for prev, curr in zip(equity_curve, equity_curve[1:])
            if isinstance(prev.get("ts"), datetime) and isinstance(curr.get("ts"), datetime)
        ]
        positive = [d for d in deltas if d > 0]
        bar_seconds = median(positive) if positive else DEFAULT_BAR_SECONDS
        return SECONDS_PER_YEAR / bar_seconds

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
                    "SELECT ts, symbol, open, high, low, close FROM price_data "
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

    def _check_exit_conditions(
        self,
        position: dict[str, Any],
        ts: datetime,
        bar: dict[str, Any],
        timeout_hours: int,
    ) -> dict[str, Any] | None:
        """Resolve stop/target against the bar's range, not just its close.

        When a bar touches both levels we cannot tell from OHLC which came
        first, so we assume the stop filled — the pessimistic convention. The
        fill is booked *at* the level, not at the close, since that is where the
        resting order would have executed.
        """
        if ts == position["entry_time"]:
            return None

        side = position["side"]
        target = float(position["target"])
        stop = float(position["stop"])
        close = float(bar.get("close") or 0.0)
        high = float(bar.get("high") or close)
        low = float(bar.get("low") or close)
        if close <= 0:
            return None

        if side == "BUY":
            if low <= stop:
                return {"reason": "stop", "price": stop}
            if high >= target:
                return {"reason": "target", "price": target}
        else:
            if high >= stop:
                return {"reason": "stop", "price": stop}
            if low <= target:
                return {"reason": "target", "price": target}

        if ts - position["entry_time"] >= timedelta(hours=timeout_hours):
            return {"reason": "timeout", "price": close}
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
