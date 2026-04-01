from __future__ import annotations

import asyncio
import csv
import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from backend.database import db_session_context, execute_raw_sql
from backend.ml.sentiment_analyzer import SentimentAnalyzer
from processing.prediction.main import PredictionEngine

router = APIRouter(prefix="/api/v1/model", tags=["model"])

_SENTIMENT_SCORE_MAP = {
    "BEARISH": 0.20,
    "FUD": 0.25,
    "NEUTRAL": 0.50,
    "BULLISH": 0.80,
}


class ModelTrainRequest(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    interval: str = Field(default="15m", pattern="^(15m|1h|4h|1d)$")
    max_rows_per_symbol: int = Field(default=6000, ge=600, le=50000)
    sentiment_sample_size: int = Field(default=30, ge=5, le=200)


@dataclass
class RuntimeInfo:
    ollama_url: str
    ollama_model: str
    ollama_reachable: bool
    cuda_available: bool
    selected_device: str
    gpu_name: str | None
    gpu_count: int


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if not symbol:
        return "BTCUSDT"
    if symbol.endswith("USDT"):
        return symbol
    return f"{symbol}USDT"


def _workspace_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sql_export_paths() -> list[Path]:
    root = _workspace_root() / "database"
    return sorted(root.glob("btc_eth_training_data_part_*.sql"))


async def _get_runtime_info() -> RuntimeInfo:
    analyzer = SentimentAnalyzer()
    ollama_reachable = False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.get(f"{analyzer.ollama_url}/api/tags")
            ollama_reachable = response.status_code == 200
    except Exception:
        ollama_reachable = False

    cuda_available = False
    gpu_count = 0
    gpu_name: str | None = None
    selected_device = "cpu"
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        gpu_count = int(torch.cuda.device_count()) if cuda_available else 0
        if gpu_count > 0:
            gpu_name = str(torch.cuda.get_device_name(0))
            selected_device = "cuda"
    except Exception:
        cuda_available = False
        gpu_count = 0
        gpu_name = None
        selected_device = "cpu"

    if not cuda_available:
        selected_device = "cpu"

    return RuntimeInfo(
        ollama_url=analyzer.ollama_url,
        ollama_model=analyzer.ollama_model,
        ollama_reachable=ollama_reachable,
        cuda_available=cuda_available,
        selected_device=selected_device,
        gpu_name=gpu_name,
        gpu_count=gpu_count,
    )


async def _load_price_frame_from_db(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT ts, open, high, low, close, volume "
                "FROM price_data WHERE symbol = :symbol AND interval = :interval "
                "ORDER BY ts DESC LIMIT :limit",
                {"symbol": symbol, "interval": interval, "limit": limit},
            )
        ).all()

    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame([dict(row._mapping) for row in rows])
    frame = frame.rename(columns={"ts": "timestamp"})
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.sort_values("timestamp").dropna(subset=["timestamp", "close"]).reset_index(drop=True)


def _parse_sql_line(line: str) -> list[str] | None:
    text = line.strip().rstrip(",")
    if not text.startswith("(") or not text.endswith(")"):
        return None
    inner = text[1:-1]
    try:
        return next(csv.reader([inner], delimiter=",", quotechar="'", skipinitialspace=True))
    except Exception:
        return None


def _load_price_frame_from_sql_exports(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    paths = _sql_export_paths()
    if not paths:
        return pd.DataFrame()

    recent_rows: deque[dict[str, Any]] = deque(maxlen=limit)
    for path in paths:
        with path.open("r", encoding="utf-8") as file_handle:
            for raw_line in file_handle:
                parsed = _parse_sql_line(raw_line)
                if not parsed or len(parsed) < 9:
                    continue

                row_symbol = parsed[1].upper()
                row_interval = parsed[2]
                if row_symbol != symbol or row_interval != interval:
                    continue

                ts = pd.to_datetime(parsed[0], utc=True, errors="coerce")
                if pd.isna(ts):
                    continue

                try:
                    recent_rows.append(
                        {
                            "timestamp": ts,
                            "open": float(parsed[3]),
                            "high": float(parsed[4]),
                            "low": float(parsed[5]),
                            "close": float(parsed[6]),
                            "volume": float(parsed[7]),
                        }
                    )
                except Exception:
                    continue

    if not recent_rows:
        return pd.DataFrame()

    frame = pd.DataFrame(list(recent_rows))
    return frame.sort_values("timestamp").reset_index(drop=True)


async def _build_finance_news_texts(symbol: str, sample_size: int) -> list[str]:
    coin = symbol.replace("USDT", "")
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT title, content FROM news_articles "
                "WHERE :coin = ANY(mentioned_coins) "
                "ORDER BY published_at DESC NULLS LAST LIMIT :limit",
                {"coin": coin, "limit": sample_size},
            )
        ).all()

    texts: list[str] = []
    for row in rows:
        title = str(row.title or "").strip()
        content = str(row.content or "").strip()
        combined = f"{title}. {content}".strip()
        if combined:
            texts.append(combined[:2000])

    if texts:
        return texts

    return [
        f"{coin} ETF flow update and derivatives positioning from major exchanges.",
        f"Macro rates outlook and impact on {coin} and broader crypto risk appetite.",
        f"Institutional order flow and liquidity conditions for {coin} in spot and futures markets.",
        f"On-chain activity and exchange reserve trend for {coin} this week.",
        f"Risk narrative and regulatory headlines that could influence {coin} sentiment.",
    ]


async def _analyze_finance_news_sentiment(symbol: str, sample_size: int) -> dict[str, Any]:
    analyzer = SentimentAnalyzer()
    texts = await _build_finance_news_texts(symbol, sample_size)

    semaphore = asyncio.Semaphore(6)

    async def _run(text: str) -> dict[str, Any]:
        async with semaphore:
            result = await analyzer.analyze(text, source_type="news")
            result["text"] = text[:220]
            return result

    results = await asyncio.gather(*[_run(text) for text in texts])

    weighted_sum = 0.0
    weight_total = 0.0
    label_counts = {"BULLISH": 0, "BEARISH": 0, "NEUTRAL": 0, "FUD": 0}

    for item in results:
        sentiment = str(item.get("sentiment", "NEUTRAL")).upper()
        confidence = float(item.get("confidence", 0.5) or 0.5)
        score = _SENTIMENT_SCORE_MAP.get(sentiment, 0.5)
        label_counts[sentiment if sentiment in label_counts else "NEUTRAL"] += 1

        weighted_sum += score * confidence
        weight_total += confidence

    aggregate_score = (weighted_sum / weight_total) if weight_total > 0 else 0.5

    return {
        "symbol": symbol,
        "sample_count": len(results),
        "aggregate_score": aggregate_score,
        "label_counts": label_counts,
        "samples": results[:8],
    }


async def _persist_sentiment_score(symbol: str, sentiment_payload: dict[str, Any]) -> None:
    score = float(sentiment_payload.get("aggregate_score", 0.5))
    label_counts = sentiment_payload.get("label_counts", {})
    dominant = "NEUTRAL"
    if isinstance(label_counts, dict) and label_counts:
        dominant = max(label_counts, key=lambda k: float(label_counts.get(k, 0)))

    async with db_session_context() as session:
        await execute_raw_sql(
            session,
            "INSERT INTO sentiment_scores "
            "(ts, symbol, source_type, source_ref_id, model_name, model_version, sentiment_label, "
            "sentiment_score, confidence, processing_latency_ms, metadata, created_at) "
            "VALUES (:ts, :symbol, :source_type, NULL, :model_name, :model_version, :sentiment_label, "
            ":sentiment_score, :confidence, 0, CAST(:metadata AS jsonb), NOW())",
            {
                "ts": datetime.now(UTC),
                "symbol": symbol.replace("USDT", ""),
                "source_type": "news",
                "model_name": "ollama_mistral_finance_news",
                "model_version": "7b",
                "sentiment_label": dominant,
                "sentiment_score": score,
                "confidence": 0.7,
                "metadata": json.dumps({"pipeline": "manual_train_endpoint", "label_counts": label_counts}),
            },
        )
        await session.commit()


async def _persist_prediction(symbol: str, interval: str, prediction: dict[str, Any], sentiment_score: float) -> None:
    current_price = float(prediction.get("current_price", 0.0) or 0.0)
    predicted_price = float(prediction.get("predicted_price", 0.0) or 0.0)
    if current_price > 0:
        predicted_return = (predicted_price - current_price) / current_price
    else:
        predicted_return = 0.0

    low = float(prediction.get("low_95", predicted_price) or predicted_price)
    high = float(prediction.get("high_95", predicted_price) or predicted_price)
    spread = max(high - low, 0.0)
    denom = max(abs(predicted_price), 1e-9)
    confidence = max(0.0, min(1.0, 1.0 - (spread / (2.0 * denom))))

    async with db_session_context() as session:
        await execute_raw_sql(
            session,
            "INSERT INTO price_predictions "
            "(ts, prediction_horizon, symbol, interval, model_name, model_version, ensemble_id, "
            "predicted_price, predicted_return, confidence, lower_bound, upper_bound, metadata, created_at) "
            "VALUES (:ts, :prediction_horizon, :symbol, :interval, :model_name, :model_version, :ensemble_id, "
            ":predicted_price, :predicted_return, :confidence, :lower_bound, :upper_bound, CAST(:metadata AS jsonb), NOW())",
            {
                "ts": datetime.now(UTC),
                "prediction_horizon": "1h",
                "symbol": symbol,
                "interval": interval,
                "model_name": "prediction_engine",
                "model_version": "finance_news_v1",
                "ensemble_id": f"manual:{int(datetime.now(UTC).timestamp())}",
                "predicted_price": predicted_price,
                "predicted_return": predicted_return,
                "confidence": confidence,
                "lower_bound": low,
                "upper_bound": high,
                "metadata": json.dumps(
                    {
                        "current_price": current_price,
                        "sentiment_score": sentiment_score,
                        "source": "api_train_with_finance_news",
                    }
                ),
            },
        )
        await session.commit()


@router.get("/runtime")
async def model_runtime() -> dict[str, Any]:
    runtime = await _get_runtime_info()
    return {
        "ollama_url": runtime.ollama_url,
        "ollama_model": runtime.ollama_model,
        "ollama_reachable": runtime.ollama_reachable,
        "cuda_available": runtime.cuda_available,
        "selected_device": runtime.selected_device,
        "gpu_name": runtime.gpu_name,
        "gpu_count": runtime.gpu_count,
        "cpu_fallback_enabled": True,
        "cpu_fallback_target": "16GB_RAM_laptop",
    }


@router.post("/train-finance-news")
async def train_finance_news(payload: ModelTrainRequest) -> dict[str, Any]:
    runtime = await _get_runtime_info()
    engine = PredictionEngine()

    results: list[dict[str, Any]] = []
    for raw_symbol in payload.symbols:
        symbol = _normalize_symbol(raw_symbol)
        price_frame = await _load_price_frame_from_db(symbol, payload.interval, payload.max_rows_per_symbol)

        data_source = "database"
        if price_frame.empty:
            price_frame = _load_price_frame_from_sql_exports(symbol, payload.interval, payload.max_rows_per_symbol)
            data_source = "sql_export"

        if price_frame.empty:
            results.append(
                {
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "No price data found in database or SQL exports",
                }
            )
            continue

        sentiment_payload = await _analyze_finance_news_sentiment(symbol, payload.sentiment_sample_size)
        sentiment_score = float(sentiment_payload["aggregate_score"])
        sentiment_df = pd.DataFrame(
            {
                "timestamp": price_frame["timestamp"],
                "sentiment_score": [sentiment_score] * len(price_frame),
            }
        )

        prediction = await asyncio.to_thread(engine.train_and_predict, symbol, price_frame, sentiment_df)
        current_price = float(price_frame["close"].iloc[-1])

        prediction_payload = {
            "symbol": prediction.symbol,
            "current_price": current_price,
            "predicted_price": float(prediction.next_price),
            "low_95": float(prediction.low_95),
            "high_95": float(prediction.high_95),
            "trained_rows": int(len(price_frame)),
            "interval": payload.interval,
            "data_source": data_source,
        }

        await _persist_sentiment_score(symbol, sentiment_payload)
        await _persist_prediction(symbol, payload.interval, prediction_payload, sentiment_score)

        results.append(
            {
                **prediction_payload,
                "sentiment": {
                    "aggregate_score": sentiment_score,
                    "sample_count": int(sentiment_payload["sample_count"]),
                    "label_counts": sentiment_payload["label_counts"],
                    "samples": sentiment_payload["samples"],
                },
                "status": "trained",
            }
        )

    return {
        "runtime": {
            "ollama_reachable": runtime.ollama_reachable,
            "cuda_available": runtime.cuda_available,
            "selected_device": runtime.selected_device,
            "gpu_name": runtime.gpu_name,
        },
        "results": results,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/cuda-plan")
async def cuda_plan(
    target_gpu_memory_gb: int = Query(default=6, ge=2, le=48),
) -> dict[str, Any]:
    return {
        "goal": "Use CUDA for Ollama/FinBERT when available, fallback to CPU on 16GB RAM laptops.",
        "checks": [
            "Install latest NVIDIA driver and confirm nvidia-smi works.",
            "Verify torch.cuda.is_available() is true.",
            "Set ENABLE_GPU=true for FinBERT path.",
            "Run Ollama with GPU enabled and verify model loads without OOM.",
        ],
        "runtime_policy": {
            "preferred_device": "cuda",
            "fallback_device": "cpu",
            "min_recommended_vram_gb": target_gpu_memory_gb,
            "cpu_target": "16GB RAM",
        },
        "optimization": [
            "Use quantized Mistral 7B model in Ollama for lower VRAM.",
            "Reduce batch size for sentiment inference when GPU memory is limited.",
            "Pin interval to 15m and cap max_rows_per_symbol for responsive retraining.",
            "Use asynchronous request batching for sentiment text processing.",
        ],
    }
