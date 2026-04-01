from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.database import db_session_context, execute_raw_sql

router = APIRouter(prefix="/api/v1/coins", tags=["coins"])


class CoinConfigIn(BaseModel):
    symbol: str = Field(min_length=2, max_length=12)
    is_enabled: bool = True
    min_signal_confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    min_signal_strength: float = Field(default=7.0, ge=0.0, le=10.0)
    max_position_size_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    tracked_intervals: list[str] = Field(default_factory=lambda: ["15m", "1h", "4h", "1d"])


class CoinConfigPatch(BaseModel):
    is_enabled: bool | None = None
    min_signal_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    min_signal_strength: float | None = Field(default=None, ge=0.0, le=10.0)
    max_position_size_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    tracked_intervals: list[str] | None = None


class CoinConfigOut(BaseModel):
    symbol: str
    is_enabled: bool
    min_signal_confidence: float
    min_signal_strength: float
    max_position_size_pct: float
    stop_loss_pct: float | None
    take_profit_pct: float | None
    tracked_intervals: list[str]
    updated_at: datetime


class CoinDetails(BaseModel):
    config: CoinConfigOut
    current_price: float | None = None
    change_24h_pct: float | None = None
    sentiment_score: float | None = None
    latest_signal: dict[str, Any] | None = None
    prediction: dict[str, Any] | None = None


def _admin_guard(x_admin_token: str | None = Header(default=None)) -> None:
    required = os.getenv("ADMIN_TOKEN", "admin")
    if x_admin_token != required:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


@router.get("/", response_model=list[CoinConfigOut])
async def list_coins(
    is_active: bool | None = Query(default=None),
    has_signals: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> list[CoinConfigOut]:
    offset = (page - 1) * page_size

    where = []
    params: dict[str, Any] = {"limit": page_size, "offset": offset}
    if is_active is not None:
        where.append("c.is_enabled = :is_active")
        params["is_active"] = is_active
    if has_signals:
        where.append("EXISTS (SELECT 1 FROM trading_signals s WHERE s.symbol = c.symbol)")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = (
        "SELECT c.symbol, c.is_enabled, c.min_signal_confidence, c.min_signal_strength, "
        "c.max_position_size_pct, c.stop_loss_pct, c.take_profit_pct, c.tracked_intervals, c.updated_at "
        "FROM coin_configs c "
        f"{where_sql} "
        "ORDER BY c.symbol ASC LIMIT :limit OFFSET :offset"
    )

    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, params)).all()

    return [CoinConfigOut(**dict(row._mapping)) for row in rows]


@router.get("/{symbol}", response_model=CoinDetails)
async def get_coin(symbol: str) -> CoinDetails:
    symbol = symbol.upper()
    symbol_pair = f"{symbol}USDT"
    async with db_session_context() as session:
        cfg = (
            await execute_raw_sql(
                session,
                "SELECT symbol, is_enabled, min_signal_confidence, min_signal_strength, "
                "max_position_size_pct, stop_loss_pct, take_profit_pct, tracked_intervals, updated_at "
                "FROM coin_configs WHERE symbol = :symbol",
                {"symbol": symbol},
            )
        ).first()
        if not cfg:
            raise HTTPException(status_code=404, detail="Coin not found")

        price_row = (
            await execute_raw_sql(
                session,
                "SELECT close, ts FROM price_data WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                {"symbol": symbol_pair},
            )
        ).first()
        prev_row = (
            await execute_raw_sql(
                session,
                "SELECT close FROM price_data WHERE symbol = :symbol AND interval = '15m' AND ts <= :cutoff "
                "ORDER BY ts DESC LIMIT 1",
                {"symbol": symbol_pair, "cutoff": datetime.now(UTC) - timedelta(hours=24)},
            )
        ).first()
        sentiment = (
            await execute_raw_sql(
                session,
                "SELECT avg_sentiment FROM sentiment_aggregates WHERE symbol = :symbol AND window = '24h' "
                "ORDER BY ts DESC LIMIT 1",
                {"symbol": symbol},
            )
        ).first()
        signal = (
            await execute_raw_sql(
                session,
                "SELECT signal, confidence, strength, ts FROM trading_signals WHERE symbol = :symbol "
                "ORDER BY ts DESC LIMIT 1",
                {"symbol": symbol},
            )
        ).first()
        prediction = (
            await execute_raw_sql(
                session,
                "SELECT predicted_price, confidence, prediction_horizon, ts FROM price_predictions "
                "WHERE symbol = :symbol ORDER BY ts DESC LIMIT 1",
                {"symbol": symbol_pair},
            )
        ).first()

    current_price = float(price_row.close) if price_row and price_row.close else None
    prev_price = float(prev_row.close) if prev_row and prev_row.close else None
    change_24h = ((current_price - prev_price) / prev_price * 100) if current_price and prev_price else None

    return CoinDetails(
        config=CoinConfigOut(**dict(cfg._mapping)),
        current_price=current_price,
        change_24h_pct=change_24h,
        sentiment_score=float(sentiment.avg_sentiment) if sentiment and sentiment.avg_sentiment is not None else None,
        latest_signal=dict(signal._mapping) if signal else None,
        prediction=dict(prediction._mapping) if prediction else None,
    )


@router.get("/{symbol}/price-history")
async def get_price_history(
    symbol: str,
    interval: str = Query(default="15m", pattern="^(15m|1h|4h|1d)$"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    pair = f"{symbol.upper()}USDT"
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT ts, open, high, low, close, volume, quote_volume, trade_count "
                "FROM price_data WHERE symbol = :symbol AND interval = :interval "
                "ORDER BY ts DESC LIMIT :limit",
                {"symbol": pair, "interval": interval, "limit": limit},
            )
        ).all()
    return [dict(row._mapping) for row in rows]


@router.get("/{symbol}/sentiment-history")
async def get_sentiment_history(
    symbol: str,
    window: str = Query(default="24h", pattern="^(1h|4h|24h)$"),
    days: int = Query(default=7, ge=1, le=90),
) -> list[dict[str, Any]]:
    symbol = symbol.upper()
    start = datetime.now(UTC) - timedelta(days=days)
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT ts, sample_count, avg_sentiment, sentiment_stddev, weighted_score "
                "FROM sentiment_aggregates WHERE symbol = :symbol AND window = :window AND ts >= :start "
                "ORDER BY ts ASC",
                {"symbol": symbol, "window": window, "start": start},
            )
        ).all()
    return [dict(row._mapping) for row in rows]


@router.get("/{symbol}/technical-indicators")
async def get_technical_indicators(symbol: str) -> dict[str, Any]:
    pair = f"{symbol.upper()}USDT"
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT ts, sma_20, sma_50, ema_12, ema_26, rsi_14, macd, macd_signal, macd_hist, "
                "bb_upper, bb_middle, bb_lower, atr_14, obv, vwap, adx_14 "
                "FROM technical_indicators WHERE symbol = :symbol AND interval = '15m' "
                "ORDER BY ts DESC LIMIT 1",
                {"symbol": pair},
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Technical indicators not found")
    return dict(row._mapping)


@router.post("/", response_model=CoinConfigOut, dependencies=[Depends(_admin_guard)])
async def add_coin(payload: CoinConfigIn) -> CoinConfigOut:
    symbol = payload.symbol.upper()
    async with db_session_context() as session:
        await execute_raw_sql(
            session,
            "INSERT INTO coin_configs (symbol, is_enabled, min_signal_confidence, min_signal_strength, "
            "max_position_size_pct, stop_loss_pct, take_profit_pct, tracked_intervals, metadata, updated_at) "
            "VALUES (:symbol, :is_enabled, :min_signal_confidence, :min_signal_strength, :max_position_size_pct, "
            ":stop_loss_pct, :take_profit_pct, :tracked_intervals, '{}'::jsonb, NOW())",
            {
                "symbol": symbol,
                "is_enabled": payload.is_enabled,
                "min_signal_confidence": payload.min_signal_confidence,
                "min_signal_strength": payload.min_signal_strength,
                "max_position_size_pct": payload.max_position_size_pct,
                "stop_loss_pct": payload.stop_loss_pct,
                "take_profit_pct": payload.take_profit_pct,
                "tracked_intervals": payload.tracked_intervals,
            },
        )
        await session.commit()

        row = (
            await execute_raw_sql(
                session,
                "SELECT symbol, is_enabled, min_signal_confidence, min_signal_strength, max_position_size_pct, "
                "stop_loss_pct, take_profit_pct, tracked_intervals, updated_at FROM coin_configs WHERE symbol = :symbol",
                {"symbol": symbol},
            )
        ).first()
    return CoinConfigOut(**dict(row._mapping))


@router.patch("/{symbol}", response_model=CoinConfigOut, dependencies=[Depends(_admin_guard)])
async def update_coin(symbol: str, payload: CoinConfigPatch) -> CoinConfigOut:
    symbol = symbol.upper()
    updates = payload.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    set_parts = []
    params: dict[str, Any] = {"symbol": symbol}
    for key, value in updates.items():
        set_parts.append(f"{key} = :{key}")
        params[key] = value
    set_parts.append("updated_at = NOW()")

    async with db_session_context() as session:
        await execute_raw_sql(
            session,
            f"UPDATE coin_configs SET {', '.join(set_parts)} WHERE symbol = :symbol",
            params,
        )
        await session.commit()
        row = (
            await execute_raw_sql(
                session,
                "SELECT symbol, is_enabled, min_signal_confidence, min_signal_strength, max_position_size_pct, "
                "stop_loss_pct, take_profit_pct, tracked_intervals, updated_at FROM coin_configs WHERE symbol = :symbol",
                {"symbol": symbol},
            )
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Coin not found")
    return CoinConfigOut(**dict(row._mapping))


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(_admin_guard)])
async def delete_coin(symbol: str) -> None:
    symbol = symbol.upper()
    async with db_session_context() as session:
        await execute_raw_sql(
            session,
            "UPDATE coin_configs SET is_enabled = false, updated_at = NOW() WHERE symbol = :symbol",
            {"symbol": symbol},
        )
        await execute_raw_sql(
            session,
            "UPDATE trading_signals SET is_active = false WHERE symbol = :symbol",
            {"symbol": symbol},
        )
        await session.commit()
