from __future__ import annotations

from typing import Any

import numpy as np
import pytest

try:
    from backend.ml.feature_engineer import FeatureEngineer
    from backend.ml.price_predictor import PricePredictor
    from backend.ml.sentiment_analyzer import SentimentAnalyzer
except Exception as exc:  # pragma: no cover - environment bootstrap guard
    pytest.skip(f"ml imports unavailable: {exc}", allow_module_level=True)


@pytest.mark.asyncio
async def test_sentiment_analysis(mock_ollama: object) -> None:
    analyzer = SentimentAnalyzer()
    result = await analyzer.analyze("Bitcoin looks very strong this week", source_type="news")
    assert result["sentiment"] in {"BULLISH", "BEARISH", "NEUTRAL", "FUD"}
    assert 0.0 <= float(result["confidence"]) <= 1.0


@pytest.mark.asyncio
async def test_feature_engineering(sample_price_data: Any) -> None:
    engineer = FeatureEngineer()
    out = await engineer.extract_price_features(sample_price_data)
    assert out is not None
    assert "log_return" in out.columns
    assert "roc_1h" in out.columns
    assert len(out) == len(sample_price_data)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_price_prediction(monkeypatch: pytest.MonkeyPatch) -> None:
    predictor = PricePredictor()

    async def _fake_vector(coin: str, timestamp: object) -> tuple[np.ndarray, list[str]]:
        return np.array([0.1] * 30), [f"f_{idx}" for idx in range(30)]

    async def _fake_prophet(coin: str, timeframe: str) -> dict[str, float]:
        return {"UP": 0.6, "DOWN": 0.2, "SIDEWAYS": 0.2}

    async def _fake_lstm(X: object) -> dict[str, float]:
        return {"UP": 0.5, "DOWN": 0.3, "SIDEWAYS": 0.2}

    async def _fake_xgb(X: object) -> dict[str, float]:
        return {"UP": 0.7, "DOWN": 0.1, "SIDEWAYS": 0.2}

    async def _fake_cache(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(predictor.feature_engineer, "create_feature_vector", _fake_vector)
    monkeypatch.setattr(predictor, "_prophet_predict_stub", _fake_prophet)
    monkeypatch.setattr(predictor.lstm, "predict", _fake_lstm)
    monkeypatch.setattr(predictor.xgb, "predict", _fake_xgb)
    monkeypatch.setattr(predictor, "_cache_prediction", _fake_cache)

    output = await predictor.predict("BTC", timeframe="1h")
    assert output["coin"] == "BTC"
    assert output["label"] in {"UP", "DOWN", "SIDEWAYS"}
    assert "ensemble" in output["predictions"]
    assert 0.0 <= float(output["confidence"]) <= 1.0

