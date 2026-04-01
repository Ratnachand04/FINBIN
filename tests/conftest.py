from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def redis_client() -> Any:
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    yield client
    client.flushall()


@pytest.fixture
def sample_reddit_data() -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [
        {
            "post_id": f"post_{idx}",
            "subreddit": "cryptocurrency",
            "title": f"BTC post {idx}",
            "body": "Bitcoin and Ethereum discussion",
            "author": "tester",
            "score": 10 + idx,
            "num_comments": 3,
            "upvote_ratio": 0.9,
            "created_utc": now - timedelta(minutes=idx),
            "url": f"https://example.com/{idx}",
            "mentioned_coins": ["BTC", "ETH"],
            "collected_at": now,
        }
        for idx in range(5)
    ]


@pytest.fixture
def sample_price_data() -> Any:
    pd = pytest.importorskip("pandas")
    now = datetime.now(UTC)
    rows = []
    base = 100.0
    for idx in range(120):
        ts = now - timedelta(minutes=15 * (120 - idx))
        open_p = base + idx * 0.2
        close_p = open_p + (0.1 if idx % 2 == 0 else -0.08)
        high_p = max(open_p, close_p) + 0.15
        low_p = min(open_p, close_p) - 0.15
        rows.append(
            {
                "ts": ts,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": 1000 + idx,
                "quote_volume": (1000 + idx) * close_p,
                "trade_count": 100 + idx,
                "macd_hist": 0.1,
                "bb_upper": close_p + 2,
                "bb_middle": close_p,
                "bb_lower": close_p - 2,
                "atr_14": 1.1,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def mock_ollama(monkeypatch: pytest.MonkeyPatch) -> Any:
    response = {"response": json.dumps({"sentiment": "BULLISH", "confidence": 0.9, "reasoning": "mocked"})}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return response

    async def _mock_post(*args: Any, **kwargs: Any) -> _Resp:
        return _Resp()

    monkeypatch.setattr("httpx.AsyncClient.post", _mock_post)
    return _mock_post


@pytest.fixture
def test_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from backend.main import app

    async def _noop_async() -> None:
        return None

    monkeypatch.setattr("backend.main.db_manager.initialize", _noop_async)
    monkeypatch.setattr("backend.main.db_manager.close", _noop_async)

    @asynccontextmanager
    async def _noop_lifespan(_app: Any):
        yield

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as client:
        yield client


@pytest.fixture
def trained_models(tmp_path: Path) -> dict[str, Path]:
    model_dir = tmp_path / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "prophet": model_dir / "prophet_v1.json",
        "lstm": model_dir / "lstm_v1.keras",
        "xgboost": model_dir / "xgboost_v1.joblib",
    }
    for _, path in paths.items():
        path.write_text("dummy-model", encoding="utf-8")
    return paths

