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

logger = logging.getLogger(__name__)


@dataclass
class ModelArtifact:
    name: str
    version: str
    path: str
    metrics: dict[str, Any]


class ProphetModel:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._prophet_mod = self._load_optional("prophet")
        self.model: Any | None = None
        self.version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def train(self, df: Any, regressors: list[str] | None = None) -> ModelArtifact:
        regressors = regressors or ["sentiment", "volume", "whale_tx"]
        if not self._prophet_mod:
            return ModelArtifact("prophet", self.version, "", {"status": "skipped"})

        Prophet = getattr(self._prophet_mod, "Prophet")
        if Prophet is None:
            return ModelArtifact("prophet", self.version, "", {"status": "unavailable"})

        train_df = df.rename(columns={"ts": "ds", "close": "y"}).copy()
        self.model = Prophet(
            daily_seasonality=True,
            weekly_seasonality=True,
            yearly_seasonality=False,
            changepoint_prior_scale=0.05,
            seasonality_mode="multiplicative",
        )

        for reg in regressors:
            if reg in train_df.columns:
                self.model.add_regressor(reg)

        await asyncio.to_thread(self.model.fit, train_df)
        model_path = self.model_dir / f"prophet_{self.version}.json"
        model_to_json = getattr(importlib.import_module("prophet.serialize"), "model_to_json")
        model_path.write_text(model_to_json(self.model), encoding="utf-8")
        return ModelArtifact("prophet", self.version, str(model_path), {"status": "trained"})

    async def predict(self, periods: int, future_df: Any) -> list[dict[str, Any]]:
        if self.model is None:
            return []
        future = future_df.rename(columns={"ts": "ds"}).copy()
        if periods > 0 and hasattr(self.model, "make_future_dataframe"):
            generated = self.model.make_future_dataframe(periods=periods, freq="H")
            future = generated.merge(future, on="ds", how="left")

        forecast = await asyncio.to_thread(self.model.predict, future)
        out: list[dict[str, Any]] = []
        for _, row in forecast.iterrows():
            out.append(
                {
                    "timestamp": row["ds"].isoformat() if hasattr(row["ds"], "isoformat") else str(row["ds"]),
                    "yhat": float(row["yhat"]),
                    "yhat_lower": float(row["yhat_lower"]),
                    "yhat_upper": float(row["yhat_upper"]),
                }
            )
        return out


class LSTMModel:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._tf = self._load_optional("tensorflow")
        self.model: Any | None = None
        self.version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    def build_architecture(self) -> Any:
        if not self._tf:
            return None

        keras = self._tf.keras
        model = keras.Sequential(
            [
                keras.layers.Input(shape=(60, 100)),
                keras.layers.LSTM(128, return_sequences=True),
                keras.layers.Dropout(0.2),
                keras.layers.LSTM(64, return_sequences=True),
                keras.layers.Dropout(0.2),
                keras.layers.LSTM(32),
                keras.layers.Dropout(0.2),
                keras.layers.Dense(3, activation="softmax"),
            ]
        )
        model.compile(optimizer=keras.optimizers.Adam(), loss="categorical_crossentropy", metrics=["accuracy"])
        self.model = model
        return model

    async def train(self, X: Any, y: Any, epochs: int = 50, validation_split: float = 0.2) -> ModelArtifact:
        if not self._tf:
            return ModelArtifact("lstm", self.version, "", {"status": "skipped"})
        if self.model is None:
            self.build_architecture()
        if self.model is None:
            return ModelArtifact("lstm", self.version, "", {"status": "unavailable"})

        keras = self._tf.keras
        callbacks = [
            keras.callbacks.EarlyStopping(patience=5, restore_best_weights=True),
            keras.callbacks.ModelCheckpoint(
                filepath=str(self.model_dir / f"lstm_best_{self.version}.keras"),
                save_best_only=True,
                monitor="val_loss",
            ),
            keras.callbacks.TensorBoard(log_dir=str(self.model_dir / "tensorboard" / self.version)),
        ]

        history = await asyncio.to_thread(
            self.model.fit,
            X,
            y,
            epochs=epochs,
            validation_split=validation_split,
            callbacks=callbacks,
            verbose=0,
        )

        model_path = self.model_dir / f"lstm_{self.version}.keras"
        await asyncio.to_thread(self.model.save, str(model_path))
        metrics = {
            "status": "trained",
            "final_loss": float(history.history.get("loss", [0])[-1]),
            "final_val_loss": float(history.history.get("val_loss", [0])[-1]),
        }
        return ModelArtifact("lstm", self.version, str(model_path), metrics)

    async def predict(self, X: Any) -> dict[str, float]:
        if self.model is None:
            return {"UP": 0.34, "DOWN": 0.33, "SIDEWAYS": 0.33}
        probabilities = await asyncio.to_thread(self.model.predict, X, verbose=0)
        probs = probabilities[0].tolist() if hasattr(probabilities, "tolist") else list(probabilities[0])
        return {"UP": float(probs[0]), "DOWN": float(probs[1]), "SIDEWAYS": float(probs[2])}


class XGBoostModel:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = model_dir
        self._xgb = self._load_optional("xgboost")
        self._joblib = self._load_optional("joblib")
        self.model: Any | None = None
        self.version = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        self.feature_importances_: dict[str, float] = {}

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def train(self, X: Any, y: Any, feature_names: list[str] | None = None) -> ModelArtifact:
        if not self._xgb:
            return ModelArtifact("xgboost", self.version, "", {"status": "skipped"})

        XGBClassifier = getattr(self._xgb, "XGBClassifier")
        self.model = XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            n_estimators=400,
            learning_rate=0.03,
            max_depth=6,
            subsample=0.85,
            colsample_bytree=0.85,
            eval_metric="mlogloss",
        )

        await asyncio.to_thread(self.model.fit, X, y)

        if feature_names and hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_.tolist()
            self.feature_importances_ = {
                feature_names[idx]: float(value)
                for idx, value in enumerate(importances)
                if idx < len(feature_names)
            }

        model_path = self.model_dir / f"xgboost_{self.version}.joblib"
        if self._joblib:
            await asyncio.to_thread(self._joblib.dump, self.model, str(model_path))
        return ModelArtifact(
            "xgboost",
            self.version,
            str(model_path),
            {"status": "trained", "feature_importances": self.feature_importances_},
        )

    async def predict(self, X: Any) -> dict[str, float]:
        if self.model is None:
            return {"UP": 0.34, "DOWN": 0.33, "SIDEWAYS": 0.33}
        probs = await asyncio.to_thread(self.model.predict_proba, X)
        row = probs[0].tolist() if hasattr(probs, "tolist") else list(probs[0])
        return {"UP": float(row[0]), "DOWN": float(row[1]), "SIDEWAYS": float(row[2])}


class PricePredictor:
    def __init__(self) -> None:
        self.model_root = Path(os.getenv("MODEL_PATH", "./models"))
        self.model_root.mkdir(parents=True, exist_ok=True)
        self.feature_engineer = FeatureEngineer()
        self.prophet = ProphetModel(self.model_root)
        self.lstm = LSTMModel(self.model_root)
        self.xgb = XGBoostModel(self.model_root)
        self._np = importlib.import_module("numpy")
        self._sk_metrics = self._load_optional("sklearn.metrics")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def train_all_models(self, coin: str, historical_data: Any) -> dict[str, Any]:
        holdout = max(1, int(len(historical_data) * 0.2))
        train_df = historical_data.iloc[:-holdout].copy() if holdout < len(historical_data) else historical_data.copy()
        test_df = historical_data.iloc[-holdout:].copy() if holdout < len(historical_data) else historical_data.copy()

        X_seq, y_seq = await self.feature_engineer.prepare_training_data(
            coin=coin,
            start_date=train_df["ts"].min(),
            end_date=train_df["ts"].max(),
        )

        if len(X_seq) == 0:
            raise ValueError("Insufficient sequence data for model training")

        X_flat = X_seq.reshape((X_seq.shape[0], -1))
        y_cat = self._to_categorical(y_seq)

        regressors = [col for col in ["sentiment", "volume", "whale_tx"] if col in train_df.columns]
        trained = await asyncio.gather(
            self.prophet.train(train_df[["ts", "close", *regressors]].copy(), regressors=regressors),
            self.lstm.train(X_seq, y_cat),
            self.xgb.train(X_flat, y_seq, feature_names=[]),
        )

        metrics = {
            artifact.name: artifact.metrics
            for artifact in trained
        }

        await self._save_model_registry(coin, trained)
        await self._cache_ab_candidate(coin, trained)

        return {
            "coin": coin,
            "models": [artifact.__dict__ for artifact in trained],
            "metrics": metrics,
            "holdout_rows": len(test_df),
        }

    async def predict(self, coin: str, timeframe: str = "1h") -> dict[str, Any]:
        timestamp = datetime.now(UTC)
        vector, feature_names = await self.feature_engineer.create_feature_vector(coin, timestamp)

        X_lstm = self._np.zeros((1, 60, 100))
        fill_count = min(100, len(vector))
        X_lstm[0, -1, :fill_count] = vector[:fill_count]
        X_xgb = X_lstm.reshape((1, -1))

        prophet_pred = await self._prophet_predict_stub(coin, timeframe)
        lstm_pred = await self.lstm.predict(X_lstm)
        xgb_pred = await self.xgb.predict(X_xgb)

        weights = {"prophet": 0.30, "lstm": 0.40, "xgb": 0.30}
        ensemble = {
            "UP": prophet_pred.get("UP", 0.0) * weights["prophet"]
            + lstm_pred.get("UP", 0.0) * weights["lstm"]
            + xgb_pred.get("UP", 0.0) * weights["xgb"],
            "DOWN": prophet_pred.get("DOWN", 0.0) * weights["prophet"]
            + lstm_pred.get("DOWN", 0.0) * weights["lstm"]
            + xgb_pred.get("DOWN", 0.0) * weights["xgb"],
            "SIDEWAYS": prophet_pred.get("SIDEWAYS", 0.0) * weights["prophet"]
            + lstm_pred.get("SIDEWAYS", 0.0) * weights["lstm"]
            + xgb_pred.get("SIDEWAYS", 0.0) * weights["xgb"],
        }

        confidence = self.calculate_ensemble_confidence([prophet_pred, lstm_pred, xgb_pred])
        label = max(ensemble, key=ensemble.get)
        response = {
            "coin": coin.upper(),
            "timeframe": timeframe,
            "timestamp": timestamp.isoformat(),
            "predictions": {
                "prophet": prophet_pred,
                "lstm": lstm_pred,
                "xgb": xgb_pred,
                "ensemble": ensemble,
            },
            "label": label,
            "confidence": confidence,
            "feature_count": len(feature_names),
        }

        await self._cache_prediction(coin, timeframe, response)
        return response

    def calculate_ensemble_confidence(self, predictions: list[dict[str, float]]) -> float:
        top_labels = [max(pred, key=pred.get) for pred in predictions]
        base_conf = sum(max(pred.values()) for pred in predictions) / max(len(predictions), 1)

        unique_count = len(set(top_labels))
        if unique_count == 1:
            base_conf *= 1.2
        elif unique_count == 2:
            base_conf *= 1.0
        else:
            base_conf *= 0.7

        return float(max(0.0, min(base_conf, 1.0)))

    async def evaluate_model(self, model: Any, X_test: Any, y_test: Any) -> dict[str, Any]:
        if not self._sk_metrics:
            return {"status": "skipped", "reason": "sklearn.metrics unavailable"}

        predictions = await asyncio.to_thread(model.predict, X_test)
        accuracy_score = getattr(self._sk_metrics, "accuracy_score")
        precision_recall_fscore_support = getattr(self._sk_metrics, "precision_recall_fscore_support")
        confusion_matrix = getattr(self._sk_metrics, "confusion_matrix")

        acc = float(accuracy_score(y_test, predictions))
        precision, recall, f1, _ = precision_recall_fscore_support(y_test, predictions, average="weighted", zero_division=0)
        cm = confusion_matrix(y_test, predictions).tolist()
        return {
            "accuracy": acc,
            "precision": float(precision),
            "recall": float(recall),
            "f1": float(f1),
            "confusion_matrix": cm,
        }

    async def _cache_prediction(self, coin: str, timeframe: str, payload: dict[str, Any]) -> None:
        key = f"prediction:{coin.upper()}:{timeframe}"
        try:
            await db_manager.redis_client.set(key, json.dumps(payload), ex=300)
        except Exception as exc:
            logger.warning("Unable to cache prediction %s: %s", key, exc)

    async def _save_model_registry(self, coin: str, artifacts: list[ModelArtifact]) -> None:
        async with db_manager.session_factory() as session:
            for art in artifacts:
                sql = (
                    "INSERT INTO model_metadata (model_name, model_version, model_type, artifact_path, is_active, metrics, metadata) "
                    "VALUES (:name, :version, :type, :path, false, CAST(:metrics AS jsonb), CAST(:meta AS jsonb)) "
                    "ON CONFLICT (model_name, model_version) DO UPDATE SET "
                    "artifact_path = EXCLUDED.artifact_path, metrics = EXCLUDED.metrics, metadata = EXCLUDED.metadata"
                )
                await execute_raw_sql(
                    session,
                    sql,
                    {
                        "name": art.name,
                        "version": art.version,
                        "type": "ensemble_component",
                        "path": art.path,
                        "metrics": json.dumps(art.metrics),
                        "meta": json.dumps({"coin": coin.upper()}),
                    },
                )
            await session.commit()

    async def _cache_ab_candidate(self, coin: str, artifacts: list[ModelArtifact]) -> None:
        key = f"abtest:candidate:{coin.upper()}"
        payload = {item.name: {"version": item.version, "path": item.path} for item in artifacts}
        try:
            await db_manager.redis_client.set(key, json.dumps(payload), ex=24 * 3600)
        except Exception as exc:
            logger.warning("Failed to cache A/B candidate metadata: %s", exc)

    def _to_categorical(self, y: Any) -> Any:
        classes = 3
        out = self._np.zeros((len(y), classes))
        for idx, value in enumerate(y):
            cls = int(value)
            cls = max(0, min(cls, classes - 1))
            out[idx, cls] = 1.0
        return out

    async def _prophet_predict_stub(self, coin: str, timeframe: str) -> dict[str, float]:
        # If Prophet is available and trained, derive directional probability from forecast slope.
        if self.prophet.model is not None:
            future_df = await self._build_future_regressors(coin, timeframe)
            forecast = await self.prophet.predict(periods=1, future_df=future_df)
            if forecast and len(forecast) >= 2:
                diff = forecast[-1]["yhat"] - forecast[-2]["yhat"]
            elif forecast:
                diff = forecast[-1]["yhat"] * 0.001
            else:
                diff = 0.0
            if diff > 0:
                return {"UP": 0.65, "DOWN": 0.15, "SIDEWAYS": 0.20}
            if diff < 0:
                return {"UP": 0.15, "DOWN": 0.65, "SIDEWAYS": 0.20}
            return {"UP": 0.20, "DOWN": 0.20, "SIDEWAYS": 0.60}

        return {"UP": 0.34, "DOWN": 0.33, "SIDEWAYS": 0.33}

    async def _build_future_regressors(self, coin: str, timeframe: str) -> Any:
        import pandas as pd

        now = datetime.now(UTC)
        rows = [{"ds": now, "sentiment": 0.0, "volume": 0.0, "whale_tx": 0.0}]
        if timeframe == "1h":
            rows.append({"ds": now + timedelta(hours=1), "sentiment": 0.0, "volume": 0.0, "whale_tx": 0.0})
        elif timeframe == "4h":
            rows.append({"ds": now + timedelta(hours=4), "sentiment": 0.0, "volume": 0.0, "whale_tx": 0.0})
        else:
            rows.append({"ds": now + timedelta(days=1), "sentiment": 0.0, "volume": 0.0, "whale_tx": 0.0})
        return pd.DataFrame(rows)
