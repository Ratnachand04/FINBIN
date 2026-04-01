from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime
from typing import Any

from prometheus_client import Counter, Gauge

from backend.collectors.news_collector import NewsCollector
from backend.collectors.onchain_collector import OnchainCollector
from backend.collectors.price_collector import PriceCollector
from backend.collectors.reddit_collector import RedditCollector
from backend.database import db_manager, execute_raw_sql

logger = logging.getLogger(__name__)

INGESTION_UP = Gauge("binfin_ingestion_worker_up", "Ingestion worker health status (1=up, 0=down)")
INGESTION_TASK_RESTARTS = Counter("binfin_ingestion_task_restarts_total", "Ingestion task restarts")
INGESTION_ERRORS = Counter("binfin_ingestion_errors_total", "Ingestion task errors", ["task"])
INGESTION_QUEUE_BACKLOG = Gauge("binfin_ingestion_queue_backlog", "Ingestion queue backlog estimate", ["source"])


class DataIngestionWorker:
    def __init__(self) -> None:
        self.reddit_collector: RedditCollector | None = None
        self.news_collector: NewsCollector | None = None
        self.onchain_collector: OnchainCollector | None = None
        self.price_collector: PriceCollector | None = None
        self._shutdown = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._error_window: list[float] = []
        self._scheduler: Any = None

        tracked = os.getenv("TRACKED_COINS", "BTC,ETH,SOL,ADA,DOT")
        self.tracked_coins = [coin.strip().upper() for coin in tracked.split(",") if coin.strip()]
        self.exchange_addresses = [
            addr.strip().lower()
            for addr in os.getenv("EXCHANGE_ADDRESSES", "").split(",")
            if addr.strip()
        ]
        self.coin_token_addresses = self._load_coin_token_addresses()

    async def run(self) -> None:
        await db_manager.initialize()
        await self._initialize_collectors()
        self._register_signal_handlers()
        self.schedule_jobs()

        self._tasks["reddit"] = asyncio.create_task(self.collect_reddit_data())
        self._tasks["price"] = asyncio.create_task(self.collect_price_data())

        INGESTION_UP.set(1)
        logger.info("DataIngestionWorker started")

        try:
            while not self._shutdown.is_set():
                await self._health_checks()
                await self._restart_failed_tasks()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Ingestion worker cancelled")
        finally:
            await self.shutdown()

    async def _initialize_collectors(self) -> None:
        self.reddit_collector = RedditCollector()
        self.news_collector = NewsCollector()
        self.onchain_collector = OnchainCollector()
        self.price_collector = PriceCollector()

    async def collect_reddit_data(self) -> None:
        if self.reddit_collector is None:
            return

        buffer: list[dict[str, Any]] = []
        while not self._shutdown.is_set():
            try:
                async for post in self.reddit_collector.stream_posts():
                    buffer.append(post)
                    if len(buffer) >= 10:
                        await self.reddit_collector.save_to_db(buffer)
                        INGESTION_QUEUE_BACKLOG.labels(source="reddit").set(0)
                        logger.info("Saved %s reddit posts", len(buffer))
                        buffer.clear()
                    if self._shutdown.is_set():
                        break
            except Exception as exc:
                await self.handle_errors("reddit", exc)
                if "rate" in str(exc).lower():
                    await asyncio.sleep(30)
                else:
                    await asyncio.sleep(5)

        if buffer:
            try:
                await self.reddit_collector.save_to_db(buffer)
            except Exception as exc:
                await self.handle_errors("reddit_flush", exc)

    async def collect_news_data(self) -> None:
        if self.news_collector is None or self._shutdown.is_set():
            return
        try:
            newsapi_rows = await self.news_collector.collect_from_newsapi()
            rss_rows = await self.news_collector.collect_from_rss()
            combined = self.news_collector.deduplicate_articles(newsapi_rows + rss_rows)
            await self.news_collector.save_to_db(combined)
            INGESTION_QUEUE_BACKLOG.labels(source="news").set(0)
            logger.info("News collection completed with %s unique articles", len(combined))
        except Exception as exc:
            await self.handle_errors("news", exc)

    async def collect_onchain_data(self) -> None:
        if self.onchain_collector is None or self._shutdown.is_set():
            return

        try:
            saved = 0
            for coin in self.tracked_coins:
                token = self.coin_token_addresses.get(coin)
                if not token:
                    continue
                txs = await self.onchain_collector.get_whale_transactions(token_address=token)
                if txs:
                    await self.onchain_collector.save_to_db(txs)
                    saved += len(txs)
                if self.exchange_addresses:
                    flows = await self.onchain_collector.get_exchange_flows(self.exchange_addresses)
                    logger.info("Exchange flows for %s: %s", coin, flows.get("windows", {}))

            INGESTION_QUEUE_BACKLOG.labels(source="onchain").set(0)
            logger.info("Onchain collection completed with %s transactions", saved)
        except Exception as exc:
            await self.handle_errors("onchain", exc)

    async def collect_price_data(self) -> None:
        if self.price_collector is None:
            return

        while not self._shutdown.is_set():
            try:
                symbols = [f"{coin}USDT" for coin in self.tracked_coins]
                await self.price_collector.stream_realtime_prices(symbols=symbols)
            except Exception as exc:
                await self.handle_errors("price_stream", exc)
                await asyncio.sleep(3)

    def schedule_jobs(self) -> None:
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except Exception as exc:
            logger.warning("APScheduler unavailable, scheduled jobs disabled: %s", exc)
            return

        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(self.collect_news_data, IntervalTrigger(minutes=30), id="news_collection", replace_existing=True)
        scheduler.add_job(self.collect_onchain_data, IntervalTrigger(minutes=15), id="onchain_collection", replace_existing=True)
        scheduler.add_job(
            self._refresh_technical_indicators,
            IntervalTrigger(minutes=15),
            id="technical_indicators",
            replace_existing=True,
        )
        scheduler.add_job(self._run_aggregations, IntervalTrigger(hours=1), id="aggregations", replace_existing=True)
        scheduler.add_job(self._cleanup_old_records, CronTrigger(hour=3, minute=0), id="cleanup", replace_existing=True)

        scheduler.start()
        self._scheduler = scheduler

    async def handle_errors(self, task_name: str, error: Exception) -> None:
        logger.exception("Ingestion task '%s' failed: %s", task_name, error)
        INGESTION_ERRORS.labels(task=task_name).inc()
        now = datetime.now(UTC).timestamp()
        self._error_window.append(now)
        self._error_window = [ts for ts in self._error_window if now - ts <= 300]

        if len(self._error_window) >= 10:
            await self._send_alert(f"High error rate detected in ingestion worker ({task_name})")

        if task_name in {"reddit", "price"} and task_name not in self._tasks:
            INGESTION_TASK_RESTARTS.inc()
            if task_name == "reddit":
                self._tasks["reddit"] = asyncio.create_task(self.collect_reddit_data())
            if task_name == "price":
                self._tasks["price"] = asyncio.create_task(self.collect_price_data())

    async def shutdown(self) -> None:
        if self._shutdown.is_set():
            return

        self._shutdown.set()
        logger.info("Shutting down ingestion worker")

        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)

        for name, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info("Cancelled task: %s", name)
            except Exception as exc:
                logger.warning("Task %s error during shutdown: %s", name, exc)

        await self._save_state()
        await self._close_collectors()
        await db_manager.close()
        INGESTION_UP.set(0)
        logger.info("Ingestion worker shutdown complete")

    async def _health_checks(self) -> None:
        db_ok = await db_manager.check_db_health()
        redis_ok = await db_manager.check_redis_health()
        INGESTION_UP.set(1 if db_ok and redis_ok and not self._shutdown.is_set() else 0)

    async def _restart_failed_tasks(self) -> None:
        for task_name, task in list(self._tasks.items()):
            if task.done() and not self._shutdown.is_set():
                try:
                    task.result()
                except Exception as exc:
                    await self.handle_errors(task_name, exc)

                INGESTION_TASK_RESTARTS.inc()
                if task_name == "reddit":
                    self._tasks[task_name] = asyncio.create_task(self.collect_reddit_data())
                elif task_name == "price":
                    self._tasks[task_name] = asyncio.create_task(self.collect_price_data())

    async def _refresh_technical_indicators(self) -> None:
        if self.price_collector is None or self._shutdown.is_set():
            return
        try:
            symbols = [f"{coin}USDT" for coin in self.tracked_coins]
            await self.price_collector._refresh_historical(symbols)
            logger.info("Technical indicators refreshed")
        except Exception as exc:
            await self.handle_errors("technical_refresh", exc)

    async def _run_aggregations(self) -> None:
        if self._shutdown.is_set():
            return
        try:
            async with db_manager.session_factory() as session:
                await execute_raw_sql(
                    session,
                    "SELECT refresh_continuous_aggregate(view_name) "
                    "FROM (VALUES ('price_data_1h'), ('price_data_4h')) AS v(view_name)",
                )
                await session.commit()
        except Exception as exc:
            await self.handle_errors("aggregations", exc)

    async def _cleanup_old_records(self) -> None:
        if self._shutdown.is_set():
            return
        try:
            async with db_manager.session_factory() as session:
                await execute_raw_sql(session, "DELETE FROM system_metrics WHERE ts < NOW() - INTERVAL '30 days'")
                await session.commit()
        except Exception as exc:
            await self.handle_errors("cleanup", exc)

    async def _send_alert(self, message: str) -> None:
        logger.error("ALERT: %s", message)

    async def _save_state(self) -> None:
        payload = {
            "tracked_coins": self.tracked_coins,
            "shutdown_at": datetime.now(UTC).isoformat(),
            "active_tasks": list(self._tasks.keys()),
        }
        try:
            await db_manager.redis_client.set("ingestion:state", json.dumps(payload), ex=86400)
        except Exception as exc:
            logger.warning("Failed to persist ingestion state: %s", exc)

    async def _close_collectors(self) -> None:
        for collector in [self.reddit_collector, self.news_collector, self.onchain_collector, self.price_collector]:
            if collector is None:
                continue
            shutdown = getattr(collector, "shutdown", None)
            if callable(shutdown):
                try:
                    maybe_coro = shutdown()
                    if asyncio.iscoroutine(maybe_coro):
                        await maybe_coro
                except Exception as exc:
                    logger.warning("Collector shutdown failed: %s", exc)

    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:
                pass

    def _load_coin_token_addresses(self) -> dict[str, str]:
        default = {
            "BTC": os.getenv("BTC_TOKEN_ADDRESS", "btc"),
            "ETH": os.getenv("ETH_TOKEN_ADDRESS", "0x0000000000000000000000000000000000000000"),
            "SOL": os.getenv("SOL_TOKEN_ADDRESS", "So11111111111111111111111111111111111111112"),
            "ADA": os.getenv("ADA_TOKEN_ADDRESS", "ada"),
            "DOT": os.getenv("DOT_TOKEN_ADDRESS", "dot"),
        }
        return default


async def _main() -> None:
    worker = DataIngestionWorker()
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(_main())
