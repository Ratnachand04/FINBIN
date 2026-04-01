from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

try:
    from backend.main import app as _APP
except Exception as exc:  # pragma: no cover - environment bootstrap guard
    pytest.skip(f"api app import unavailable: {exc}", allow_module_level=True)


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def all(self) -> list[Any]:
        return [type("Row", (), {"_mapping": row, **row}) for row in self._rows]

    def first(self) -> Any:
        if not self._rows:
            return None
        row = self._rows[0]
        return type("Row", (), {"_mapping": row, **row})


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> Any:
    @asynccontextmanager
    async def _noop_lifespan(_app: Any):
        yield

    _APP.router.lifespan_context = _noop_lifespan
    async def _noop_async() -> None:
        return None

    monkeypatch.setattr("backend.main.db_manager.initialize", _noop_async)
    monkeypatch.setattr("backend.main.db_manager.close", _noop_async)

    with TestClient(_APP) as client:
        yield client


@pytest.mark.integration
def test_get_coins(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_exec(session: Any, sql: str, params: dict[str, Any] | None = None) -> FakeResult:
        return FakeResult(
            [
                {
                    "symbol": "BTC",
                    "is_enabled": True,
                    "min_signal_confidence": 0.75,
                    "min_signal_strength": 7.0,
                    "max_position_size_pct": 0.1,
                    "stop_loss_pct": None,
                    "take_profit_pct": None,
                    "tracked_intervals": ["15m", "1h"],
                    "updated_at": datetime.now(UTC),
                }
            ]
        )

    @asynccontextmanager
    async def _ctx():
        class _Session:
            pass

        yield _Session()

    monkeypatch.setattr("backend.api.coins.db_session_context", _ctx)
    monkeypatch.setattr("backend.api.coins.execute_raw_sql", _fake_exec)

    response = api_client.get("/api/v1/coins/")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert payload[0]["symbol"] == "BTC"


@pytest.mark.integration
def test_generate_signal(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_generate(symbol: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "signal": "BUY",
            "confidence": 0.9,
            "strength": 8.1,
            "entry_price": 40000,
            "take_profit": 41000,
            "stop_loss": 39500,
            "factors": {},
            "metadata": {},
        }

    monkeypatch.setattr("backend.api.signals.generate_signal_async", _fake_generate)
    response = api_client.post("/api/v1/signals/generate", json={"coin": "BTC", "force": True})
    assert response.status_code == 200
    payload = response.json()
    assert "task_id" in payload
    assert payload["status"] in {"queued", "running", "completed"}


@pytest.mark.integration
def test_backtest_run(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class _BacktestResult:
        run_id = 1
        metrics = {"win_rate": 0.6}
        report = {"summary": "ok"}
        trades = [{"id": 1}]

    async def _fake_run(*args: Any, **kwargs: Any) -> Any:
        return _BacktestResult()

    monkeypatch.setattr("backend.api.backtest.BacktestEngine.run_backtest", _fake_run)

    payload = {
        "start_date": "2025-01-01T00:00:00Z",
        "end_date": "2025-02-01T00:00:00Z",
        "coins": ["BTC"],
        "strategy_config": {"initial_capital": 10000},
    }
    response = api_client.post("/api/v1/backtest/run", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert result["run_id"] == 1
    assert result["trades_count"] == 1

