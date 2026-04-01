from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from backend.database import db_session_context, execute_raw_sql

router = APIRouter(prefix="/api/v1/sentiment", tags=["sentiment"])


@router.get("/")
async def get_sentiment(
    symbol: str | None = Query(default=None),
    hours: int = Query(default=24, ge=1, le=720),
    limit: int = Query(default=500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    where = ["ts >= :start"]
    params: dict[str, Any] = {
        "start": datetime.now(UTC) - timedelta(hours=hours),
        "limit": limit,
    }
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper()

    sql = (
        "SELECT id, source_type, source_id, symbol, sentiment_score, confidence, reasoning, model_name, ts "
        "FROM sentiment_scores "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ts DESC LIMIT :limit"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, params)).all()
    return [dict(row._mapping) for row in rows]


@router.get("/aggregate")
async def get_sentiment_aggregate(
    symbol: str,
    window: str = Query(default="24h", pattern="^(1h|4h|24h)$"),
    days: int = Query(default=7, ge=1, le=90),
) -> list[dict[str, Any]]:
    start = datetime.now(UTC) - timedelta(days=days)
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT ts, symbol, window, sample_count, avg_sentiment, sentiment_stddev, weighted_score "
                "FROM sentiment_aggregates WHERE symbol = :symbol AND window = :window AND ts >= :start "
                "ORDER BY ts ASC",
                {"symbol": symbol.upper(), "window": window, "start": start},
            )
        ).all()
    return [dict(row._mapping) for row in rows]
