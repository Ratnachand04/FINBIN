from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

from backend.database import db_manager, execute_raw_sql
from backend.ml.price_predictor import PricePredictor
from backend.ml.sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)

ML_WORKER_UP = Gauge("binfin_ml_worker_up", "ML processing worker health status (1=up, 0=down)")
ML_QUEUE_BACKLOG = Gauge("binfin_ml_queue_backlog", "Queue backlog", ["queue"])
ML_INFER_LATENCY = Histogram("binfin_ml_infer_latency_seconds", "Inference latency", ["pipeline"])
ML_ERRORS = Counter("binfin_ml_errors_total", "ML worker errors", ["pipeline"])
ML_MODEL_VERSION = Gauge("binfin_ml_model_version_info", "Loaded model version marker", ["model", "variant"])


class MLProcessingWorker:
    def __init__(self) -> None:
        self.sentiment_analyzer: SentimentAnalyzer | None = None
        self.predictor_a: PricePredictor | None = None
        self.predictor_b: PricePredictor | None = None
        self._shutdown = asyncio.Event()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._psutil = self._load_optional("psutil")
        self._ab_ratio = float(os.getenv("PREDICTION_AB_RATIO", "0.5"))
        tracked = os.getenv("TRACKED_COINS", "BTC,ETH,DOGE")
        self.tracked_coins = [coin.strip().upper() for coin in tracked.split(",") if coin.strip()]

    async def run(self) -> None:
        await db_manager.initialize()
        await self.load_models()

        self._tasks["sentiment_queue"] = asyncio.create_task(self.process_sentiment_queue())
        self._tasks["prediction_queue"] = asyncio.create_task(self.process_prediction_queue())
        self._tasks["sentiment_scheduled"] = asyncio.create_task(self.scheduled_sentiment_processing())
        self._tasks["prediction_scheduled"] = asyncio.create_task(self.scheduled_prediction())
        self._tasks["performance"] = asyncio.create_task(self.monitor_performance())

        ML_WORKER_UP.set(1)
        logger.info("MLProcessingWorker started")

        try:
            while not self._shutdown.is_set():
                await self._monitor_queue_sizes()
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("ML worker cancelled")
        finally:
            await self.shutdown()

    async def load_models(self) -> None:
        self.sentiment_analyzer = SentimentAnalyzer()
        self.predictor_a = PricePredictor()
        self.predictor_b = PricePredictor()

        if self.sentiment_analyzer is not None:
            try:
                await self.sentiment_analyzer.analyze("Warmup text for sentiment model", source_type="other")
            except Exception as exc:
                logger.warning("Sentiment warmup failed: %s", exc)

        warm_coin = self.tracked_coins[0] if self.tracked_coins else "BTC"
        for variant, predictor in (("A", self.predictor_a), ("B", self.predictor_b)):
            if predictor is None:
                continue
            try:
                await predictor.predict(warm_coin, timeframe="1h")
            except Exception as exc:
                logger.warning("Predictor %s warmup failed: %s", variant, exc)

            ML_MODEL_VERSION.labels(model="predictor", variant=variant).set(float(int(time.time()) % 10_000))

    async def process_sentiment_queue(self) -> None:
        if self.sentiment_analyzer is None:
            return

        queue = "sentiment:pending"
        while not self._shutdown.is_set():
            try:
                items = await self._dequeue_batch(queue, max_items=10, timeout=5)
                if not items:
                    continue

                texts = [item.get("text", "") for item in items]
                with ML_INFER_LATENCY.labels(pipeline="sentiment").time():
                    results = await self.sentiment_analyzer.batch_analyze(texts, batch_size=10)

                await self._save_sentiment_results(items, results)
                for raw_item, result in zip(items, results):
                    payload = {
                        "symbol": raw_item.get("symbol"),
                        "source_type": raw_item.get("source_type", "other"),
                        "result": result,
                    }
                    await db_manager.redis_client.publish("sentiment:completed", json.dumps(payload, default=str))

                await self.update_sentiment_aggregates()
            except Exception as exc:
                ML_ERRORS.labels(pipeline="sentiment_queue").inc()
                logger.exception("Sentiment queue processing failed: %s", exc)
                await asyncio.sleep(2)

    async def process_prediction_queue(self) -> None:
        queue = "prediction:pending"
        while not self._shutdown.is_set():
            try:
                items = await self._dequeue_batch(queue, max_items=20, timeout=5)
                if not items:
                    continue

                for item in items:
                    coin = str(item.get("coin", "")).upper()
                    if not coin:
                        continue
                    predictor = self._choose_predictor(coin)
                    if predictor is None:
                        continue

                    with ML_INFER_LATENCY.labels(pipeline="prediction").time():
                        pred = await predictor.predict(coin=coin, timeframe=str(item.get("timeframe", "1h")))

                    await self._save_prediction(coin, pred, model_variant=item.get("variant", "auto"))
                    await db_manager.redis_client.publish("prediction:completed", json.dumps(pred, default=str))
            except Exception as exc:
                ML_ERRORS.labels(pipeline="prediction_queue").inc()
                logger.exception("Prediction queue processing failed: %s", exc)
                await asyncio.sleep(2)

    async def scheduled_sentiment_processing(self) -> None:
        while not self._shutdown.is_set():
            try:
                await self._process_recent_unprocessed_texts()
                await self.update_sentiment_aggregates()
            except Exception as exc:
                ML_ERRORS.labels(pipeline="sentiment_scheduled").inc()
                logger.exception("Scheduled sentiment processing failed: %s", exc)
            await asyncio.sleep(15 * 60)

    async def scheduled_prediction(self) -> None:
        while not self._shutdown.is_set():
            try:
                for coin in self.tracked_coins:
                    predictor = self._choose_predictor(coin)
                    if predictor is None:
                        continue
                    for horizon in ("1h", "4h", "24h"):
                        pred = await predictor.predict(coin=coin, timeframe=horizon)
                        await self._save_prediction(coin, pred, model_variant="scheduled")
            except Exception as exc:
                ML_ERRORS.labels(pipeline="prediction_scheduled").inc()
                logger.exception("Scheduled prediction failed: %s", exc)
            await asyncio.sleep(15 * 60)

    async def update_sentiment_aggregates(self) -> None:
        windows = {
            "1h": "1 hour",
            "4h": "4 hours",
            "24h": "24 hours",
            "7d": "7 days",
        }

        async with db_manager.session_factory() as session:
            symbols_rows = (await execute_raw_sql(session, "SELECT DISTINCT symbol FROM sentiment_scores")).all()
            symbols = [str(row.symbol) for row in symbols_rows if row.symbol]

            for symbol in symbols:
                for window_key, interval_literal in windows.items():
                    row = (
                        await execute_raw_sql(
                            session,
                            "SELECT "
                            "COUNT(*)::int AS sample_count, "
                            "COALESCE(AVG(sentiment_score),0) AS avg_sentiment, "
                            "STDDEV_POP(sentiment_score) AS sentiment_stddev, "
                            "AVG(CASE WHEN sentiment_label = 'BULLISH' THEN 1 ELSE 0 END) AS bullish_ratio, "
                            "AVG(CASE WHEN sentiment_label = 'BEARISH' THEN 1 ELSE 0 END) AS bearish_ratio, "
                            "AVG(CASE WHEN sentiment_label = 'NEUTRAL' THEN 1 ELSE 0 END) AS neutral_ratio, "
                            "COALESCE(AVG(sentiment_score * COALESCE(confidence, 0.5)),0) AS weighted_score "
                            "FROM sentiment_scores "
                            "WHERE symbol = :symbol AND ts >= NOW() - CAST(:interval_literal AS interval)",
                            {"symbol": symbol, "interval_literal": interval_literal},
                        )
                    ).first()

                    if row is None or int(row.sample_count or 0) == 0:
                        continue

                    ts = datetime.now(UTC).replace(second=0, microsecond=0)
                    await execute_raw_sql(
                        session,
                        "INSERT INTO sentiment_aggregates "
                        "(ts, window, symbol, sample_count, avg_sentiment, sentiment_stddev, bullish_ratio, "
                        "bearish_ratio, neutral_ratio, weighted_score, metadata, created_at) "
                        "VALUES (:ts, :window, :symbol, :sample_count, :avg_sentiment, :sentiment_stddev, :bullish_ratio, "
                        ":bearish_ratio, :neutral_ratio, :weighted_score, CAST(:metadata AS jsonb), NOW()) "
                        "ON CONFLICT (window, symbol, ts) DO UPDATE SET "
                        "sample_count = EXCLUDED.sample_count, avg_sentiment = EXCLUDED.avg_sentiment, "
                        "sentiment_stddev = EXCLUDED.sentiment_stddev, bullish_ratio = EXCLUDED.bullish_ratio, "
                        "bearish_ratio = EXCLUDED.bearish_ratio, neutral_ratio = EXCLUDED.neutral_ratio, "
                        "weighted_score = EXCLUDED.weighted_score, metadata = EXCLUDED.metadata",
                        {
                            "ts": ts,
                            "window": window_key,
                            "symbol": symbol,
                            "sample_count": int(row.sample_count),
                            "avg_sentiment": float(row.avg_sentiment or 0.0),
                            "sentiment_stddev": float(row.sentiment_stddev or 0.0),
                            "bullish_ratio": float(row.bullish_ratio or 0.0),
                            "bearish_ratio": float(row.bearish_ratio or 0.0),
                            "neutral_ratio": float(row.neutral_ratio or 0.0),
                            "weighted_score": float(row.weighted_score or 0.0),
                            "metadata": json.dumps({"updated_by": "ml_worker"}),
                        },
                    )

            await session.commit()

    async def monitor_performance(self) -> None:
        while not self._shutdown.is_set():
            try:
                cpu_percent = memory_percent = None
                if self._psutil is not None:
                    cpu_percent = float(self._psutil.cpu_percent(interval=None))
                    memory_percent = float(self._psutil.virtual_memory().percent)

                queues = ["sentiment:pending", "prediction:pending"]
                queue_depths: dict[str, int] = {}
                for queue_name in queues:
                    depth = int(await db_manager.redis_client.llen(queue_name))
                    queue_depths[queue_name] = depth
                    ML_QUEUE_BACKLOG.labels(queue=queue_name).set(depth)

                await self._write_system_metric(
                    service_name="ml_processor",
                    queue_depth=sum(queue_depths.values()),
                    metadata={
                        "cpu_percent": cpu_percent,
                        "memory_percent": memory_percent,
                        "queue_depths": queue_depths,
                    },
                )
            except Exception as exc:
                ML_ERRORS.labels(pipeline="performance_monitor").inc()
                logger.warning("Performance monitoring failed: %s", exc)
            await asyncio.sleep(30)

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
                logger.info("Cancelled ML task: %s", name)
            except Exception as exc:
                logger.warning("Error while stopping task %s: %s", name, exc)

        await db_manager.close()
        ML_WORKER_UP.set(0)
        logger.info("MLProcessingWorker shutdown complete")

    async def _dequeue_batch(self, queue: str, max_items: int, timeout: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        first = await db_manager.redis_client.blpop(queue, timeout=timeout)
        if not first:
            return items

        _, payload = first
        items.append(self._parse_queue_payload(payload))

        while len(items) < max_items:
            payload = await db_manager.redis_client.lpop(queue)
            if payload is None:
                break
            items.append(self._parse_queue_payload(payload))
        return items

    def _parse_queue_payload(self, payload: str | bytes) -> dict[str, Any]:
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
        return {"text": raw, "source_type": "other", "symbol": "BTC"}

    def _choose_predictor(self, coin: str) -> PricePredictor | None:
        if self.predictor_a is None and self.predictor_b is None:
            return None
        if self.predictor_a is None:
            return self.predictor_b
        if self.predictor_b is None:
            return self.predictor_a

        bucket = (sum(ord(ch) for ch in coin.upper()) % 100) / 100.0
        return self.predictor_a if bucket < self._ab_ratio else self.predictor_b

    async def _save_sentiment_results(self, queue_items: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
        async with db_manager.session_factory() as session:
            for item, result in zip(queue_items, results):
                symbol = str(item.get("symbol", "BTC")).upper()
                source_type = str(item.get("source_type", "other")).lower()
                if source_type not in {"reddit", "news", "onchain", "other"}:
                    source_type = "other"
                score_map = {"BEARISH": 0.2, "FUD": 0.25, "NEUTRAL": 0.5, "BULLISH": 0.8}
                label = str(result.get("sentiment", "NEUTRAL")).upper()
                score = float(result.get("score", score_map.get(label, 0.5)))
                confidence = float(result.get("confidence", 0.5))
                model_name = str(result.get("engine", "sentiment_analyzer"))

                await execute_raw_sql(
                    session,
                    "INSERT INTO sentiment_scores "
                    "(ts, symbol, source_type, source_ref_id, model_name, model_version, sentiment_label, sentiment_score, "
                    "confidence, processing_latency_ms, metadata, created_at) "
                    "VALUES (:ts, :symbol, :source_type, :source_ref_id, :model_name, :model_version, :sentiment_label, "
                    ":sentiment_score, :confidence, :processing_latency_ms, CAST(:metadata AS jsonb), NOW())",
                    {
                        "ts": datetime.now(UTC),
                        "symbol": symbol,
                        "source_type": source_type,
                        "source_ref_id": item.get("source_ref_id"),
                        "model_name": model_name,
                        "model_version": str(item.get("model_version", "v1")),
                        "sentiment_label": label,
                        "sentiment_score": score,
                        "confidence": confidence,
                        "processing_latency_ms": int(item.get("latency_ms", 0) or 0),
                        "metadata": json.dumps({"queue_item": item, "raw_result": result}),
                    },
                )
            await session.commit()

    async def _save_prediction(self, coin: str, pred: dict[str, Any], model_variant: str) -> None:
        ensemble = pred.get("predictions", {}).get("ensemble", {})
        up = float(ensemble.get("UP", 0.0))
        down = float(ensemble.get("DOWN", 0.0))
        confidence = float(pred.get("confidence", 0.0))
        current_price = float(pred.get("current_price") or 0.0)

        direction = 1 if up >= down else -1
        predicted_return = confidence * 0.01 * direction
        predicted_price = current_price * (1 + predicted_return) if current_price > 0 else None

        async with db_manager.session_factory() as session:
            await execute_raw_sql(
                session,
                "INSERT INTO price_predictions "
                "(ts, prediction_horizon, symbol, interval, model_name, model_version, ensemble_id, predicted_price, "
                "predicted_return, confidence, metadata, created_at) "
                "VALUES (:ts, :prediction_horizon, :symbol, :interval, :model_name, :model_version, :ensemble_id, "
                ":predicted_price, :predicted_return, :confidence, CAST(:metadata AS jsonb), NOW())",
                {
                    "ts": datetime.now(UTC),
                    "prediction_horizon": pred.get("timeframe", "1h"),
                    "symbol": f"{coin.upper()}USDT",
                    "interval": "15m",
                    "model_name": "ensemble",
                    "model_version": "ab-test",
                    "ensemble_id": f"{model_variant}:{int(time.time())}",
                    "predicted_price": predicted_price,
                    "predicted_return": predicted_return,
                    "confidence": confidence,
                    "metadata": json.dumps({"prediction": pred, "variant": model_variant}),
                },
            )
            await session.commit()

    async def _process_recent_unprocessed_texts(self) -> None:
        if self.sentiment_analyzer is None:
            return
        cutoff = datetime.now(UTC) - timedelta(minutes=15)

        async with db_manager.session_factory() as session:
            news_rows = (
                await execute_raw_sql(
                    session,
                    "SELECT title, content, mentioned_coins FROM news_data WHERE ts >= :cutoff ORDER BY ts DESC LIMIT 50",
                    {"cutoff": cutoff},
                )
            ).all()

            reddit_rows = (
                await execute_raw_sql(
                    session,
                    "SELECT title, body, mentioned_coins FROM reddit_data WHERE created_utc >= :cutoff ORDER BY created_utc DESC LIMIT 50",
                    {"cutoff": cutoff},
                )
            ).all()

        queue_items: list[dict[str, Any]] = []
        for row in news_rows:
            coins = list(row.mentioned_coins or [])
            queue_items.append(
                {
                    "text": f"{row.title or ''}\n{row.content or ''}".strip(),
                    "source_type": "news",
                    "symbol": (coins[0] if coins else "BTC"),
                }
            )
        for row in reddit_rows:
            coins = list(row.mentioned_coins or [])
            queue_items.append(
                {
                    "text": f"{row.title or ''}\n{row.body or ''}".strip(),
                    "source_type": "reddit",
                    "symbol": (coins[0] if coins else "BTC"),
                }
            )

        if not queue_items:
            return

        queue_items = queue_items[:100]
        texts = [item["text"] for item in queue_items]
        results = await self.sentiment_analyzer.batch_analyze(texts, batch_size=10)
        await self._save_sentiment_results(queue_items, results)

    async def _monitor_queue_sizes(self) -> None:
        for queue in ("sentiment:pending", "prediction:pending"):
            depth = int(await db_manager.redis_client.llen(queue))
            ML_QUEUE_BACKLOG.labels(queue=queue).set(depth)

    async def _write_system_metric(self, service_name: str, queue_depth: int, metadata: dict[str, Any]) -> None:
        async with db_manager.session_factory() as session:
            await execute_raw_sql(
                session,
                "INSERT INTO system_metrics (ts, service_name, queue_depth, metadata, created_at) "
                "VALUES (:ts, :service_name, :queue_depth, CAST(:metadata AS jsonb), NOW())",
                {
                    "ts": datetime.now(UTC),
                    "service_name": service_name,
                    "queue_depth": queue_depth,
                    "metadata": json.dumps(metadata),
                },
            )
            await session.commit()

    def _load_optional(self, module_name: str) -> Any:
        try:
            return __import__(module_name)
        except Exception:
            return None


async def _main() -> None:
    worker = MLProcessingWorker()
    await worker.run()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(_main())
