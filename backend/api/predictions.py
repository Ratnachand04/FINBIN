from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from backend.database import db_session_context, execute_raw_sql

router = APIRouter(prefix="/api/v1/predictions", tags=["predictions"])


@router.get("/")
async def list_predictions(
    symbol: str | None = Query(default=None),
    horizon: str | None = Query(default=None),
    hours: int = Query(default=72, ge=1, le=1440),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    where = ["ts >= :start"]
    params: dict[str, Any] = {
        "start": datetime.now(UTC) - timedelta(hours=hours),
        "limit": limit,
    }
    if symbol:
        where.append("symbol = :symbol")
        params["symbol"] = symbol.upper() if not symbol.upper().endswith("USDT") else symbol.upper()
    if horizon:
        where.append("prediction_horizon = :horizon")
        params["horizon"] = horizon

    sql = (
        "SELECT id, symbol, current_price, predicted_price, confidence, prediction_horizon, model_version, ts "
        "FROM price_predictions "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY ts DESC LIMIT :limit"
    )
    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, params)).all()
    return [dict(row._mapping) for row in rows]
