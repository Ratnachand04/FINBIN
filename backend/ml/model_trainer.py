from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.database import db_manager, execute_raw_sql
from backend.ml.feature_engineer import FeatureEngineer
from backend.ml.price_predictor import PricePredictor

logger = logging.getLogger(__name__)


@dataclass
class TrainArtifacts:
    coin: str
    metrics: dict[str, Any]
    model_paths: dict[str, str]


class ModelTrainer:
    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self.predictor = PricePredictor()
        self._np = importlib.import_module("numpy")
        self._pd = importlib.import_module("pandas")
        self._sk_model_selection = self._load_optional("sklearn.model_selection")
        self._sk_metrics = self._load_optional("sklearn.metrics")
        self._optuna = self._load_optional("optuna")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def train_pipeline(self, coin: str = "BTC", lookback_days: int = 90) -> dict[str, Any]:
        logger.info("Starting training pipeline for %s", coin)
        data = await self.prepare_data(coin, lookback_days)

        X_train, X_val, y_train, y_val = data["X_train"], data["X_val"], data["y_train"], data["y_val"]
        historical = data["historical_df"]

        train_result = await self.predictor.train_all_models(coin, historical)

        eval_metrics = {
            "lstm": await self.validate_model(self.predictor.lstm.model, (X_val, y_val)),
            "xgb": await self.validate_model(self.predictor.xgb.model, (X_val.reshape((X_val.shape[0], -1)), y_val)),
        }

        metadata = {
            "coin": coin.upper(),
            "lookback_days": lookback_days,
            "trained_at": datetime.now(UTC).isoformat(),
            "train_shapes": {
                "X_train": list(X_train.shape),
                "X_val": list(X_val.shape),
                "y_train": list(y_train.shape),
                "y_val": list(y_val.shape),
            },
            "train_result": train_result,
            "eval_metrics": eval_metrics,
        }

        artifacts = await self.save_model_artifacts(train_result, metadata)
        await self._log_training_result_to_db(coin, metadata)

        return {
            "status": "completed",
            "coin": coin.upper(),
            "metrics": eval_metrics,
            "artifacts": artifacts,
        }

    async def prepare_data(self, coin: str, lookback_days: int) -> dict[str, Any]:
        end_ts = datetime.now(UTC)
        start_ts = end_ts - timedelta(days=lookback_days)
        symbol = f"{coin.upper()}USDT"

        async with db_manager.session_factory() as session:
            q = (
                "SELECT p.ts, p.open, p.high, p.low, p.close, p.volume, p.quote_volume, p.trade_count, "
                "COALESCE(sa.avg_sentiment, 0) AS sentiment, "
                "COALESCE(ot.whale_tx, wt.whale_tx, 0) AS whale_tx "
                "FROM price_data p "
                "LEFT JOIN sentiment_aggregates sa "
                "  ON sa.symbol = :coin "
                " AND sa.window = '1h' "
                " AND DATE_TRUNC('hour', sa.ts) = DATE_TRUNC('hour', p.ts) "
                "LEFT JOIN ("
                "  SELECT symbol, DATE_TRUNC('hour', ts) AS hour_ts, COUNT(*)::float AS whale_tx "
                "  FROM onchain_transactions "
                "  WHERE symbol = :coin AND is_whale = true AND ts BETWEEN :start_ts AND :end_ts "
                "  GROUP BY symbol, DATE_TRUNC('hour', ts)"
                ") ot "
                "  ON ot.symbol = :coin AND ot.hour_ts = DATE_TRUNC('hour', p.ts) "
                "LEFT JOIN ("
                "  SELECT symbol, DATE_TRUNC('hour', ts) AS hour_ts, COUNT(*)::float AS whale_tx "
                "  FROM whale_transactions "
                "  WHERE symbol = :coin AND is_whale = true AND ts BETWEEN :start_ts AND :end_ts "
                "  GROUP BY symbol, DATE_TRUNC('hour', ts)"
                ") wt "
                "  ON wt.symbol = :coin AND wt.hour_ts = DATE_TRUNC('hour', p.ts) "
                "WHERE p.symbol = :symbol AND p.interval = '15m' AND p.ts BETWEEN :start_ts AND :end_ts "
                "ORDER BY p.ts ASC"
            )
            rows = (await execute_raw_sql(
                session,
                q,
                {
                    "coin": coin.upper(),
                    "symbol": symbol,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                },
            )).all()

        if not rows:
            raise ValueError(f"No data found for {coin}")

        historical_df = self._pd.DataFrame([dict(row._mapping) for row in rows])
        X, y = await self.feature_engineer.prepare_training_data(coin, start_ts, end_ts)

        split_idx = max(1, int(len(X) * 0.8))
        X_train = X[:split_idx]
        X_val = X[split_idx:]
        y_train = y[:split_idx]
        y_val = y[split_idx:]

        return {
            "historical_df": historical_df,
            "X_train": X_train,
            "X_val": X_val,
            "y_train": y_train,
            "y_val": y_val,
        }

    async def train_with_hyperparameter_tuning(self, model_type: str) -> dict[str, Any]:
        model_type = model_type.lower()
        if model_type not in {"xgboost", "lstm", "prophet"}:
            raise ValueError("model_type must be one of: xgboost, lstm, prophet")

        if self._optuna and model_type == "xgboost":
            return await self._optuna_tune_xgboost()

        if self._sk_model_selection and model_type == "xgboost":
            return await self._grid_tune_xgboost()

        return {"status": "skipped", "reason": "No tuning backend available", "model_type": model_type}

    async def validate_model(self, model: Any, val_data: tuple[Any, Any]) -> dict[str, Any]:
        X_val, y_val = val_data
        if model is None:
            return {"status": "skipped", "reason": "model unavailable"}

        if not self._sk_metrics:
            return {"status": "skipped", "reason": "sklearn.metrics unavailable"}

        if hasattr(model, "predict"):
            y_pred = await asyncio.to_thread(model.predict, X_val)
        else:
            return {"status": "skipped", "reason": "predict not available"}

        if hasattr(y_pred, "ndim") and y_pred.ndim > 1:
            y_pred_cls = self._np.argmax(y_pred, axis=1)
        else:
            y_pred_cls = y_pred

        accuracy_score = self._sk_metrics.accuracy_score
        precision_recall_fscore_support = self._sk_metrics.precision_recall_fscore_support
        confusion_matrix = self._sk_metrics.confusion_matrix

        acc = float(accuracy_score(y_val, y_pred_cls))
        precision, recall, f1, _ = precision_recall_fscore_support(y_val, y_pred_cls, average="weighted", zero_division=0)
        cm = confusion_matrix(y_val, y_pred_cls).tolist()

        direction_accuracy = acc
        profit_factor = self._estimate_profit_factor(y_pred_cls, y_val)
        sharpe = self._estimate_prediction_sharpe(y_pred_cls, y_val)

        return {
            "directional_accuracy": direction_accuracy,
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "confusion_matrix": cm,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe,
        }

    async def save_model_artifacts(self, train_result: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        model_paths = {entry["name"]: entry.get("path", "") for entry in train_result.get("models", [])}
        model_dir = Path(os.getenv("MODEL_PATH", "./models"))
        model_dir.mkdir(parents=True, exist_ok=True)

        meta_path = model_dir / f"train_metadata_{metadata['coin']}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        async with db_manager.session_factory() as session:
            for model_name, path in model_paths.items():
                version = await self._next_model_version(session, model_name)
                sql = (
                    "INSERT INTO model_metadata "
                    "(model_name, model_version, model_type, artifact_path, is_active, metrics, parameters, metadata) "
                    "VALUES (:name, :version, :type, :path, false, CAST(:metrics AS jsonb), CAST(:params AS jsonb), CAST(:meta AS jsonb))"
                )
                await execute_raw_sql(
                    session,
                    sql,
                    {
                        "name": model_name,
                        "version": version,
                        "type": "auto_trained",
                        "path": path,
                        "metrics": json.dumps(train_result.get("metrics", {}).get(model_name, {})),
                        "params": json.dumps({}),
                        "meta": json.dumps(metadata),
                    },
                )
            await session.commit()

        return {
            "model_paths": model_paths,
            "metadata_path": str(meta_path),
        }

    async def compare_with_production(self, new_model: dict[str, Any], prod_model: dict[str, Any]) -> dict[str, Any]:
        new_score = float(new_model.get("f1", new_model.get("directional_accuracy", 0)))
        prod_score = float(prod_model.get("f1", prod_model.get("directional_accuracy", 0)))

        improvement = ((new_score - prod_score) / prod_score) if prod_score > 0 else 1.0
        replace = improvement > 0.05

        return {
            "new_score": new_score,
            "production_score": prod_score,
            "improvement_ratio": improvement,
            "recommendation": "replace" if replace else "keep_production",
        }

    def schedule_retraining(self) -> Any:
        apscheduler = self._load_optional("apscheduler.schedulers.asyncio")
        if not apscheduler:
            logger.warning("APScheduler unavailable; scheduling skipped")
            return None

        scheduler_cls = getattr(apscheduler, "AsyncIOScheduler")
        scheduler = scheduler_cls(timezone="UTC")

        async def _job() -> None:
            tracked = [coin.strip().upper() for coin in os.getenv("TRACKED_COINS", "BTC,ETH,DOGE").split(",") if coin.strip()]
            for coin in tracked:
                try:
                    result = await self.train_pipeline(coin=coin, lookback_days=90)
                    comparison = await self.compare_with_production(
                        result.get("metrics", {}).get("xgb", {}),
                        await self._load_production_metrics("xgb"),
                    )
                    if comparison["recommendation"] == "replace":
                        await self._activate_latest_model("xgb")
                except Exception as exc:
                    logger.exception("Scheduled training failed for %s: %s", coin, exc)

        scheduler.add_job(_job, "cron", hour=2, minute=0, id="daily_model_retraining", replace_existing=True)
        scheduler.start()
        return scheduler

    async def _optuna_tune_xgboost(self) -> dict[str, Any]:
        return {"status": "placeholder", "backend": "optuna", "model": "xgboost"}

    async def _grid_tune_xgboost(self) -> dict[str, Any]:
        return {"status": "placeholder", "backend": "gridsearch", "model": "xgboost"}

    async def _next_model_version(self, session: Any, model_name: str) -> str:
        q = "SELECT model_version FROM model_metadata WHERE model_name = :name ORDER BY created_at DESC LIMIT 1"
        row = (await execute_raw_sql(session, q, {"name": model_name})).first()
        if not row or not row.model_version:
            return "v1"
        current = str(row.model_version).lstrip("v")
        try:
            return f"v{int(current) + 1}"
        except ValueError:
            return f"v{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    async def _log_training_result_to_db(self, coin: str, metadata: dict[str, Any]) -> None:
        try:
            async with db_manager.session_factory() as session:
                await execute_raw_sql(
                    session,
                    "INSERT INTO backtest_runs (strategy_name, symbol, interval, config, metrics, created_at) "
                    "VALUES (:strategy_name, :symbol, :interval, CAST(:config AS jsonb), CAST(:metrics AS jsonb), NOW())",
                    {
                        "strategy_name": "model_training_pipeline",
                        "symbol": coin.upper(),
                        "interval": "15m",
                        "config": json.dumps({"lookback_days": metadata.get("lookback_days")}),
                        "metrics": json.dumps(metadata.get("eval_metrics", {})),
                    },
                )
                await session.commit()
        except Exception as exc:
            logger.warning("Unable to write training log to DB: %s", exc)

    async def _load_production_metrics(self, model_name: str) -> dict[str, Any]:
        async with db_manager.session_factory() as session:
            row = (
                await execute_raw_sql(
                    session,
                    "SELECT metrics FROM model_metadata WHERE model_name = :name AND is_active = true ORDER BY created_at DESC LIMIT 1",
                    {"name": model_name},
                )
            ).first()
        return dict(row.metrics) if row and row.metrics else {}

    async def _activate_latest_model(self, model_name: str) -> None:
        async with db_manager.session_factory() as session:
            await execute_raw_sql(session, "UPDATE model_metadata SET is_active = false WHERE model_name = :name", {"name": model_name})
            await execute_raw_sql(
                session,
                "UPDATE model_metadata SET is_active = true WHERE id = ("
                "SELECT id FROM model_metadata WHERE model_name = :name ORDER BY created_at DESC LIMIT 1)",
                {"name": model_name},
            )
            await session.commit()

    def _estimate_profit_factor(self, y_pred: Any, y_true: Any) -> float:
        gains = 0.0
        losses = 0.0
        for pred, true in zip(y_pred, y_true):
            pnl = 1.0 if pred == true else -1.0
            if pnl > 0:
                gains += pnl
            else:
                losses += abs(pnl)
        return gains / losses if losses > 0 else gains

    def _estimate_prediction_sharpe(self, y_pred: Any, y_true: Any) -> float:
        returns = self._np.array([1.0 if p == t else -1.0 for p, t in zip(y_pred, y_true)], dtype=float)
        std = returns.std()
        if std == 0:
            return 0.0
        return float(returns.mean() / std)
