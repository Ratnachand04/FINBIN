from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter, Gauge

from backend.database import db_manager, execute_raw_sql
from backend.signal.generator import SignalGenerator

logger = logging.getLogger(__name__)

SIGNAL_WORKER_UP = Gauge("binfin_signal_worker_up", "Signal generation worker health status")
SIGNAL_GEN_COUNT = Counter("binfin_signal_generated_total", "Signals generated", ["signal"])
SIGNAL_NOTIFY_COUNT = Counter("binfin_signal_notifications_total", "Signal notifications sent", ["channel"])
SIGNAL_ERRORS = Counter("binfin_signal_worker_errors_total", "Signal worker errors", ["task"])


class SignalGeneratorWorker:
    def __init__(self) -> None:
        self.generator = SignalGenerator()
        self._shutdown = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._last_data_ts: dict[str, datetime] = {}
        tracked = os.getenv("TRACKED_COINS", "BTC,ETH,SOL,ADA,DOT")
        self.tracked_coins = [coin.strip().upper() for coin in tracked.split(",") if coin.strip()]
        self.signal_strength_threshold = float(os.getenv("SIGNAL_MIN_STRENGTH", "6.5"))
        self.signal_conf_threshold = float(os.getenv("SIGNAL_MIN_CONFIDENCE", "0.70"))
        self.high_conf_threshold = float(os.getenv("SIGNAL_HIGH_CONFIDENCE", "0.85"))
        self.rate_limit_seconds = int(os.getenv("SIGNAL_NOTIFY_RATE_LIMIT_SECONDS", "20"))
        self.notification_preferences = {
            "websocket": os.getenv("NOTIFY_WEBSOCKET", "true").lower() == "true",
            "desktop": os.getenv("NOTIFY_DESKTOP", "true").lower() == "true",
            "telegram": os.getenv("NOTIFY_TELEGRAM", "false").lower() == "true",
            "email": os.getenv("NOTIFY_EMAIL", "false").lower() == "true",
        }
        self._last_notification_at: datetime | None = None

    async def run(self) -> None:
        await db_manager.initialize()
        self._register_signal_handlers()

        self._tasks["generation"] = asyncio.create_task(self.generation_loop())
        self._tasks["monitor_existing"] = asyncio.create_task(self.monitor_existing_signals())
        self._tasks["outcomes"] = asyncio.create_task(self.update_signal_outcomes())
        self._tasks["cleanup"] = asyncio.create_task(self.cleanup_old_signals())

        SIGNAL_WORKER_UP.set(1)
        logger.info("SignalGeneratorWorker started")

        try:
            while not self._shutdown.is_set():
                await self._restart_failed_tasks()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Signal worker cancelled")
        finally:
            await self.shutdown()

    async def generation_loop(self) -> None:
        while not self._shutdown.is_set():
            start = datetime.now(UTC)
            try:
                for coin in self.tracked_coins:
                    if self._shutdown.is_set():
                        break
                    if not await self._has_new_data(coin):
                        continue
                    generated = await self.generate_signal_for_coin(coin)
                    if generated:
                        await self.send_notifications(generated)
            except Exception as exc:
                await self._handle_error("generation_loop", exc)

            elapsed = (datetime.now(UTC) - start).total_seconds()
            await asyncio.sleep(max(5.0, 900 - elapsed))

    async def generate_signal_for_coin(self, coin: str) -> dict[str, Any] | None:
        data_ok = await self._load_latest_data(coin)
        if not data_ok:
            return None

        signal = await self.generator.generate_signal(coin)
        signal_type = str(signal.get("signal", "HOLD")).upper()
        confidence = float(signal.get("confidence", 0.0) or 0.0)
        strength = float(signal.get("strength", 0.0) or 0.0)

        if signal_type not in {"BUY", "SELL"}:
            return None
        if confidence < self.signal_conf_threshold or strength < self.signal_strength_threshold:
            logger.info("Signal below quality thresholds for %s: conf=%.3f strength=%.2f", coin, confidence, strength)
            return None
        if await self.check_cooldown(coin, signal_type):
            logger.info("Cooldown active for %s %s", coin, signal_type)
            return None

        SIGNAL_GEN_COUNT.labels(signal=signal_type).inc()
        return signal

    async def check_cooldown(self, coin: str, signal_type: str) -> bool:
        async with db_manager.session_factory() as session:
            row = (
                await execute_raw_sql(
                    session,
                    "SELECT ts FROM trading_signals "
                    "WHERE symbol = :symbol AND signal = :signal "
                    "ORDER BY ts DESC LIMIT 1",
                    {"symbol": coin.upper(), "signal": signal_type.upper()},
                )
            ).first()

        if not row:
            return False
        last_ts = row.ts if isinstance(row.ts, datetime) else None
        if not last_ts:
            return False
        return (datetime.now(UTC) - last_ts) < timedelta(hours=1)

    async def send_notifications(self, signal: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        if self._last_notification_at and (now - self._last_notification_at).total_seconds() < self.rate_limit_seconds:
            return

        confidence = float(signal.get("confidence", 0.0) or 0.0)
        if confidence < self.high_conf_threshold:
            return

        symbol = str(signal.get("symbol", "UNK"))
        payload = {
            "type": "high_conf_signal",
            "symbol": symbol,
            "signal": signal.get("signal"),
            "confidence": confidence,
            "strength": signal.get("strength"),
            "timestamp": datetime.now(UTC).isoformat(),
        }

        if self.notification_preferences["websocket"]:
            try:
                await db_manager.redis_client.publish("signals:notifications", json.dumps(payload))
                SIGNAL_NOTIFY_COUNT.labels(channel="websocket").inc()
            except Exception as exc:
                await self._handle_error("notify_websocket", exc)

        if self.notification_preferences["desktop"]:
            logger.info("Desktop notification: %s %s conf=%.2f", symbol, payload["signal"], confidence)
            SIGNAL_NOTIFY_COUNT.labels(channel="desktop").inc()

        if self.notification_preferences["telegram"]:
            await self._send_telegram(payload)
            SIGNAL_NOTIFY_COUNT.labels(channel="telegram").inc()

        if self.notification_preferences["email"]:
            await self._send_email(payload)
            SIGNAL_NOTIFY_COUNT.labels(channel="email").inc()

        self._last_notification_at = now
        logger.info("Signal notifications sent for %s", symbol)

    async def monitor_existing_signals(self) -> None:
        while not self._shutdown.is_set():
            try:
                async with db_manager.session_factory() as session:
                    rows = (
                        await execute_raw_sql(
                            session,
                            "SELECT id, symbol, signal, entry_price, stop_loss, take_profit, expires_at, metadata "
                            "FROM trading_signals WHERE is_active = true",
                        )
                    ).all()

                for row in rows:
                    signal_row = dict(row._mapping)
                    close_reason = await self._evaluate_close_reason(signal_row)
                    if close_reason is not None:
                        await self._close_signal(signal_row, close_reason)
            except Exception as exc:
                await self._handle_error("monitor_existing", exc)

            await asyncio.sleep(60)

    async def update_signal_outcomes(self) -> None:
        while not self._shutdown.is_set():
            try:
                async with db_manager.session_factory() as session:
                    rows = (
                        await execute_raw_sql(
                            session,
                            "SELECT id, symbol, signal, metadata FROM trading_signals "
                            "WHERE is_active = false "
                            "AND (metadata->>'closed_at') IS NOT NULL "
                            "AND COALESCE((metadata->>'outcome_updated')::boolean, false) = false "
                            "ORDER BY ts DESC LIMIT 200",
                        )
                    ).all()

                    for row in rows:
                        data = dict(row._mapping)
                        md = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                        pnl = float(md.get("pnl_pct", 0.0) or 0.0)
                        md["outcome_updated"] = True
                        md["outcome_bucket"] = "win" if pnl > 0 else "loss" if pnl < 0 else "flat"

                        await execute_raw_sql(
                            session,
                            "UPDATE trading_signals SET metadata = CAST(:metadata AS jsonb) WHERE id = :id",
                            {"id": int(data["id"]), "metadata": json.dumps(md)},
                        )

                    await session.commit()
            except Exception as exc:
                await self._handle_error("update_outcomes", exc)

            await asyncio.sleep(600)

    async def cleanup_old_signals(self) -> None:
        while not self._shutdown.is_set():
            try:
                async with db_manager.session_factory() as session:
                    rows = (
                        await execute_raw_sql(
                            session,
                            "SELECT id, ts, symbol, signal, confidence, strength, metadata "
                            "FROM trading_signals WHERE ts < NOW() - INTERVAL '30 days'",
                        )
                    ).all()

                    if rows:
                        archive_rows = []
                        for row in rows:
                            data = dict(row._mapping)
                            archive_rows.append(
                                {
                                    "signal_id": int(data["id"]),
                                    "ts": data.get("ts").isoformat() if data.get("ts") else None,
                                    "symbol": data.get("symbol"),
                                    "signal": data.get("signal"),
                                    "confidence": float(data.get("confidence") or 0.0),
                                    "strength": float(data.get("strength") or 0.0),
                                    "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
                                }
                            )

                        await db_manager.redis_client.set("signals:archive:last", json.dumps(archive_rows, default=str), ex=86400)
                        await execute_raw_sql(session, "DELETE FROM trading_signals WHERE ts < NOW() - INTERVAL '30 days'")
                        await session.commit()
            except Exception as exc:
                await self._handle_error("cleanup", exc)

            await asyncio.sleep(3600)

    async def shutdown(self) -> None:
        if self._shutdown.is_set():
            return
        self._shutdown.set()

        for task in self._tasks.values():
            task.cancel()
        for name, task in self._tasks.items():
            try:
                await task
            except asyncio.CancelledError:
                logger.info("Cancelled signal task: %s", name)
            except Exception as exc:
                logger.warning("Task %s failed during shutdown: %s", name, exc)

        await db_manager.close()
        SIGNAL_WORKER_UP.set(0)
        logger.info("SignalGeneratorWorker shutdown complete")

    async def _has_new_data(self, coin: str) -> bool:
        symbol = f"{coin.upper()}USDT"
        async with db_manager.session_factory() as session:
            row = (
                await execute_raw_sql(
                    session,
                    "SELECT MAX(ts) AS latest_ts FROM price_data WHERE symbol = :symbol AND interval = '15m'",
                    {"symbol": symbol},
                )
            ).first()

        latest_ts = row.latest_ts if row else None
        if not latest_ts:
            return False
        previous = self._last_data_ts.get(coin.upper())
        self._last_data_ts[coin.upper()] = latest_ts
        return previous is None or latest_ts > previous

    async def _load_latest_data(self, coin: str) -> bool:
        symbol = f"{coin.upper()}USDT"
        async with db_manager.session_factory() as session:
            checks = []
            checks.append(
                (
                    await execute_raw_sql(
                        session,
                        "SELECT 1 FROM price_data WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                        {"symbol": symbol},
                    )
                ).first()
            )
            checks.append(
                (
                    await execute_raw_sql(
                        session,
                        "SELECT 1 FROM technical_indicators WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                        {"symbol": symbol},
                    )
                ).first()
            )
            checks.append(
                (
                    await execute_raw_sql(
                        session,
                        "SELECT 1 FROM sentiment_aggregates WHERE symbol = :coin AND window = '24h' ORDER BY ts DESC LIMIT 1",
                        {"coin": coin.upper()},
                    )
                ).first()
            )
            checks.append(
                (
                    await execute_raw_sql(
                        session,
                        "SELECT 1 FROM price_predictions WHERE symbol = :symbol ORDER BY ts DESC LIMIT 1",
                        {"symbol": symbol},
                    )
                ).first()
            )
            checks.append(
                (
                    await execute_raw_sql(
                        session,
                        "SELECT 1 FROM onchain_transactions WHERE symbol = :coin ORDER BY ts DESC LIMIT 1",
                        {"coin": coin.upper()},
                    )
                ).first()
            )
        return all(item is not None for item in checks)

    async def _evaluate_close_reason(self, signal_row: dict[str, Any]) -> str | None:
        signal_type = str(signal_row.get("signal", "")).upper()
        symbol = str(signal_row.get("symbol", "")).upper()
        if not symbol:
            return None

        async with db_manager.session_factory() as session:
            price_row = (
                await execute_raw_sql(
                    session,
                    "SELECT close FROM price_data WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                    {"symbol": f"{symbol}USDT" if not symbol.endswith("USDT") else symbol},
                )
            ).first()
        if not price_row or price_row.close is None:
            return None

        price = float(price_row.close)
        tp = float(signal_row.get("take_profit") or 0.0)
        sl = float(signal_row.get("stop_loss") or 0.0)
        expires_at = signal_row.get("expires_at")

        if signal_type == "BUY":
            if tp > 0 and price >= tp:
                return "take_profit"
            if sl > 0 and price <= sl:
                return "stop_loss"
        elif signal_type == "SELL":
            if tp > 0 and price <= tp:
                return "take_profit"
            if sl > 0 and price >= sl:
                return "stop_loss"

        if isinstance(expires_at, datetime) and datetime.now(UTC) >= expires_at:
            return "timeout"
        return None

    async def _close_signal(self, signal_row: dict[str, Any], reason: str) -> None:
        signal_id = int(signal_row["id"])
        symbol = str(signal_row.get("symbol", "")).upper()

        async with db_manager.session_factory() as session:
            price_row = (
                await execute_raw_sql(
                    session,
                    "SELECT close FROM price_data WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                    {"symbol": f"{symbol}USDT" if not symbol.endswith("USDT") else symbol},
                )
            ).first()
            if not price_row or price_row.close is None:
                return

            close_price = float(price_row.close)
            entry_price = float(signal_row.get("entry_price") or 0.0)
            signal_type = str(signal_row.get("signal", "")).upper()
            pnl_pct = 0.0
            if entry_price > 0:
                if signal_type == "BUY":
                    pnl_pct = ((close_price - entry_price) / entry_price) * 100
                elif signal_type == "SELL":
                    pnl_pct = ((entry_price - close_price) / entry_price) * 100

            metadata = signal_row.get("metadata") if isinstance(signal_row.get("metadata"), dict) else {}
            metadata.update(
                {
                    "closed_at": datetime.now(UTC).isoformat(),
                    "close_price": close_price,
                    "close_reason": reason,
                    "pnl_pct": pnl_pct,
                }
            )

            await execute_raw_sql(
                session,
                "UPDATE trading_signals SET is_active = false, metadata = CAST(:metadata AS jsonb) WHERE id = :id",
                {"id": signal_id, "metadata": json.dumps(metadata)},
            )
            await session.commit()

    async def _send_telegram(self, payload: dict[str, Any]) -> None:
        # Hook for optional telegram integration.
        logger.info("Telegram notification queued: %s", payload.get("symbol"))

    async def _send_email(self, payload: dict[str, Any]) -> None:
        # Hook for optional email integration.
        logger.info("Email notification queued: %s", payload.get("symbol"))

    async def _restart_failed_tasks(self) -> None:
        for name, task in list(self._tasks.items()):
            if task.done() and not self._shutdown.is_set():
                try:
                    task.result()
                except Exception as exc:
                    await self._handle_error(name, exc)

                if name == "generation":
                    self._tasks[name] = asyncio.create_task(self.generation_loop())
                elif name == "monitor_existing":
                    self._tasks[name] = asyncio.create_task(self.monitor_existing_signals())
                elif name == "outcomes":
                    self._tasks[name] = asyncio.create_task(self.update_signal_outcomes())
                elif name == "cleanup":
                    self._tasks[name] = asyncio.create_task(self.cleanup_old_signals())

    async def _handle_error(self, task_name: str, exc: Exception) -> None:
        SIGNAL_ERRORS.labels(task=task_name).inc()
        logger.exception("Signal worker error in %s: %s", task_name, exc)

    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:
                pass


async def _main() -> None:
    worker = SignalGeneratorWorker()
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(_main())
