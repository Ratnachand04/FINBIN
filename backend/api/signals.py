from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.database import db_session_context, execute_raw_sql
from backend.signal.generator import generate_signal_async

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/signals", tags=["signals"])

_TASKS: dict[str, dict[str, Any]] = {}
_WS_CLIENTS: set[WebSocket] = set()


class TradingSignal(BaseModel):
    id: int
    ts: datetime
    symbol: str
    interval: str
    signal: str
    strength: float
    confidence: float
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    horizon_minutes: int | None = None
    rationale: str | None = None
    is_active: bool
    expires_at: datetime | None = None
    risk_level: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignalOutcome(BaseModel):
    signal_id: int
    symbol: str
    signal: str
    executed_at: datetime | None = None
    closed_at: datetime | None = None
    execution_price: float | None = None
    close_price: float | None = None
    pnl_pct: float | None = None
    close_reason: str | None = None


class TradingSignalDetail(TradingSignal):
    factors: dict[str, Any] = Field(default_factory=dict)
    explanation: str | None = None
    outcome: SignalOutcome | None = None


class GenerateSignalRequest(BaseModel):
    coin: str = Field(min_length=2, max_length=12)
    force: bool = False


class ExecuteSignalRequest(BaseModel):
    execution_price: float = Field(gt=0)
    timestamp: datetime | None = None


class CloseSignalRequest(BaseModel):
    close_price: float = Field(gt=0)
    close_reason: str = Field(min_length=2, max_length=250)
    timestamp: datetime | None = None


class SignalPerformance(BaseModel):
    group_by: str
    rows: list[dict[str, Any]]


def _normalize_row(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    metadata = data.get("metadata")
    factors = data.get("factors")

    if isinstance(metadata, str):
        try:
            data["metadata"] = json.loads(metadata)
        except Exception:
            data["metadata"] = {}
    elif metadata is None:
        data["metadata"] = {}

    if isinstance(factors, str):
        try:
            data["factors"] = json.loads(factors)
        except Exception:
            data["factors"] = {}
    elif factors is None:
        data["factors"] = {}

    risk_level = data.get("metadata", {}).get("risk_level")
    if risk_level is not None:
        data["risk_level"] = risk_level

    for key in ("entry_price", "stop_loss", "take_profit"):
        value = data.get(key)
        if isinstance(value, Decimal):
            data[key] = float(value)
    return data


def _build_outcome(signal_data: dict[str, Any]) -> SignalOutcome | None:
    md = signal_data.get("metadata", {})
    if not isinstance(md, dict):
        return None
    if not md.get("closed_at") and not md.get("executed_at"):
        return None

    return SignalOutcome(
        signal_id=int(signal_data["id"]),
        symbol=str(signal_data["symbol"]),
        signal=str(signal_data["signal"]),
        executed_at=md.get("executed_at"),
        closed_at=md.get("closed_at"),
        execution_price=md.get("execution_price"),
        close_price=md.get("close_price"),
        pnl_pct=md.get("pnl_pct"),
        close_reason=md.get("close_reason"),
    )


async def _broadcast(event: str, payload: dict[str, Any]) -> None:
    if not _WS_CLIENTS:
        return
    packet = {
        "event": event,
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": payload,
    }
    stale: list[WebSocket] = []
    for ws in _WS_CLIENTS:
        try:
            await ws.send_json(packet)
        except Exception:
            stale.append(ws)
    for ws in stale:
        _WS_CLIENTS.discard(ws)


@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket) -> None:
    await websocket.accept()
    _WS_CLIENTS.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _WS_CLIENTS.discard(websocket)


@router.get("/", response_model=list[TradingSignal])
async def list_signals(
    coin: str | None = Query(default=None),
    signal_type: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    min_strength: float | None = Query(default=None, ge=0.0, le=10.0),
    is_active: bool | None = Query(default=None),
    sort_by: Literal["timestamp", "strength", "confidence"] = Query(default="timestamp"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> list[TradingSignal]:
    where: list[str] = []
    params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}

    if coin:
        where.append("symbol = :symbol")
        params["symbol"] = coin.upper()
    if signal_type:
        where.append("signal = :signal")
        params["signal"] = signal_type.upper()
    if min_confidence is not None:
        where.append("confidence >= :min_confidence")
        params["min_confidence"] = min_confidence
    if min_strength is not None:
        where.append("strength >= :min_strength")
        params["min_strength"] = min_strength
    if is_active is not None:
        where.append("is_active = :is_active")
        params["is_active"] = is_active

    order_field = {
        "timestamp": "ts",
        "strength": "strength",
        "confidence": "confidence",
    }[sort_by]
    order_sql = "ASC" if sort_order == "asc" else "DESC"
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = (
        "SELECT id, ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
        "horizon_minutes, rationale, is_active, expires_at, metadata "
        "FROM trading_signals "
        f"{where_sql} "
        f"ORDER BY {order_field} {order_sql} "
        "LIMIT :limit OFFSET :offset"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, params)).all()
    return [TradingSignal(**_normalize_row(row)) for row in rows]


@router.get("/active", response_model=list[TradingSignal])
async def get_active_signals(limit: int = Query(default=200, ge=1, le=1000)) -> list[TradingSignal]:
    sql = (
        "SELECT id, ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
        "horizon_minutes, rationale, is_active, expires_at, metadata "
        "FROM trading_signals "
        "WHERE is_active = true "
        "AND ((metadata->>'executed_at') IS NULL OR ts >= NOW() - INTERVAL '1 hour') "
        "ORDER BY ts DESC LIMIT :limit"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, {"limit": limit})).all()
    return [TradingSignal(**_normalize_row(row)) for row in rows]


@router.get("/{signal_id}", response_model=TradingSignalDetail)
async def get_signal(signal_id: int) -> TradingSignalDetail:
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT id, ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
                "horizon_minutes, factors, rationale, is_active, expires_at, metadata "
                "FROM trading_signals WHERE id = :id",
                {"id": signal_id},
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")

    data = _normalize_row(row)
    data["explanation"] = data.get("rationale")
    data["outcome"] = _build_outcome(data)
    return TradingSignalDetail(**data)


@router.post("/generate")
async def generate_signal(payload: GenerateSignalRequest) -> dict[str, str]:
    task_id = str(uuid4())
    _TASKS[task_id] = {
        "status": "queued",
        "coin": payload.coin.upper(),
        "created_at": datetime.now(UTC).isoformat(),
    }

    async def _runner() -> None:
        coin = payload.coin.upper()
        try:
            _TASKS[task_id]["status"] = "running"
            result = await generate_signal_async(coin)
            _TASKS[task_id]["status"] = "completed"
            _TASKS[task_id]["result"] = result
            await _broadcast("signal_generated", {"task_id": task_id, "signal": result})
        except Exception as exc:
            logger.exception("Signal generation task failed: %s", exc)
            _TASKS[task_id]["status"] = "failed"
            _TASKS[task_id]["error"] = str(exc)

    if payload.force:
        asyncio.create_task(_runner())
    else:
        asyncio.create_task(_runner())

    return {"task_id": task_id, "status": _TASKS[task_id]["status"]}


@router.post("/{signal_id}/execute", response_model=TradingSignal)
async def execute_signal(signal_id: int, payload: ExecuteSignalRequest) -> TradingSignal:
    ts = payload.timestamp or datetime.now(UTC)
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT id, metadata FROM trading_signals WHERE id = :id",
                {"id": signal_id},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Signal not found")

        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        metadata.update(
            {
                "executed_at": ts.isoformat(),
                "execution_price": payload.execution_price,
            }
        )
        await execute_raw_sql(
            session,
            "UPDATE trading_signals SET metadata = CAST(:metadata AS jsonb), created_at = created_at WHERE id = :id",
            {"id": signal_id, "metadata": json.dumps(metadata)},
        )
        updated = (
            await execute_raw_sql(
                session,
                "SELECT id, ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
                "horizon_minutes, rationale, is_active, expires_at, metadata FROM trading_signals WHERE id = :id",
                {"id": signal_id},
            )
        ).first()
        await session.commit()

    signal = TradingSignal(**_normalize_row(updated))
    await _broadcast("signal_executed", signal.model_dump())
    return signal


@router.post("/{signal_id}/close", response_model=TradingSignal)
async def close_signal(signal_id: int, payload: CloseSignalRequest) -> TradingSignal:
    close_ts = payload.timestamp or datetime.now(UTC)
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT id, signal, entry_price, metadata FROM trading_signals WHERE id = :id",
                {"id": signal_id},
            )
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Signal not found")

        metadata = row.metadata if isinstance(row.metadata, dict) else {}
        execution_price = float(metadata.get("execution_price") or (float(row.entry_price) if row.entry_price else 0.0))
        if execution_price <= 0:
            raise HTTPException(status_code=400, detail="Signal has no execution/entry price")

        signal_type = str(row.signal).upper()
        if signal_type == "BUY":
            pnl_pct = ((payload.close_price - execution_price) / execution_price) * 100
        elif signal_type == "SELL":
            pnl_pct = ((execution_price - payload.close_price) / execution_price) * 100
        else:
            pnl_pct = 0.0

        metadata.update(
            {
                "closed_at": close_ts.isoformat(),
                "close_price": payload.close_price,
                "close_reason": payload.close_reason,
                "pnl_pct": pnl_pct,
            }
        )
        await execute_raw_sql(
            session,
            "UPDATE trading_signals SET is_active = false, metadata = CAST(:metadata AS jsonb) WHERE id = :id",
            {"id": signal_id, "metadata": json.dumps(metadata)},
        )

        updated = (
            await execute_raw_sql(
                session,
                "SELECT id, ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
                "horizon_minutes, rationale, is_active, expires_at, metadata FROM trading_signals WHERE id = :id",
                {"id": signal_id},
            )
        ).first()
        await session.commit()

    signal = TradingSignal(**_normalize_row(updated))
    await _broadcast("signal_closed", signal.model_dump())
    return signal


@router.get("/performance", response_model=SignalPerformance)
async def get_signal_performance(
    group_by: Literal["coin", "timeframe", "risk_level"] = Query(default="coin"),
) -> SignalPerformance:
    group_expr = {
        "coin": "symbol",
        "timeframe": "interval",
        "risk_level": "COALESCE(metadata->>'risk_level', 'UNKNOWN')",
    }[group_by]

    sql = (
        "SELECT "
        f"{group_expr} AS group_key, "
        "COUNT(*) FILTER (WHERE (metadata->>'closed_at') IS NOT NULL) AS total_trades, "
        "AVG(CASE WHEN (metadata->>'pnl_pct') IS NOT NULL THEN (metadata->>'pnl_pct')::double precision END) AS avg_return, "
        "AVG(CASE WHEN ((metadata->>'pnl_pct')::double precision) > 0 THEN 1 ELSE 0 END) AS win_rate "
        "FROM trading_signals "
        "WHERE (metadata->>'closed_at') IS NOT NULL "
        "GROUP BY group_key ORDER BY total_trades DESC"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql)).all()

    output = []
    for row in rows:
        item = dict(row._mapping)
        output.append(
            {
                "group": item["group_key"],
                "total_trades": int(item.get("total_trades") or 0),
                "avg_return": float(item.get("avg_return") or 0.0),
                "win_rate": float(item.get("win_rate") or 0.0),
            }
        )
    return SignalPerformance(group_by=group_by, rows=output)


@router.get("/recent-outcomes", response_model=list[SignalOutcome])
async def recent_outcomes() -> list[SignalOutcome]:
    sql = (
        "SELECT id, symbol, signal, metadata FROM trading_signals "
        "WHERE (metadata->>'closed_at') IS NOT NULL "
        "ORDER BY (metadata->>'closed_at')::timestamptz DESC LIMIT 20"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql)).all()

    outcomes: list[SignalOutcome] = []
    for row in rows:
        data = dict(row._mapping)
        md = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        outcomes.append(
            SignalOutcome(
                signal_id=int(data["id"]),
                symbol=str(data["symbol"]),
                signal=str(data["signal"]),
                executed_at=md.get("executed_at"),
                closed_at=md.get("closed_at"),
                execution_price=md.get("execution_price"),
                close_price=md.get("close_price"),
                pnl_pct=md.get("pnl_pct"),
                close_reason=md.get("close_reason"),
            )
        )
    return outcomes
