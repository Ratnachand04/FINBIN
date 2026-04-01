from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.backtest.engine import BacktestEngine
from backend.database import db_session_context, execute_raw_sql

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


class BacktestRunRequest(BaseModel):
    start_date: datetime
    end_date: datetime
    coins: list[str] = Field(default_factory=list)
    strategy_config: dict[str, Any] = Field(default_factory=dict)


@router.post("/run")
async def run_backtest(payload: BacktestRunRequest) -> dict[str, Any]:
    if payload.start_date >= payload.end_date:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    engine = BacktestEngine()
    result = await engine.run_backtest(
        start_date=payload.start_date,
        end_date=payload.end_date,
        coins=[coin.upper() for coin in payload.coins],
        strategy_config=payload.strategy_config,
    )
    return {
        "run_id": result.run_id,
        "metrics": result.metrics,
        "report": result.report,
        "trades_count": len(result.trades),
    }


@router.get("/runs")
async def list_backtest_runs(limit: int = 50) -> list[dict[str, Any]]:
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT id, strategy_name, parameters, start_date, end_date, total_return, sharpe_ratio, "
                "max_drawdown, win_rate, total_trades, created_at "
                "FROM backtest_results ORDER BY created_at DESC LIMIT :limit",
                {"limit": limit},
            )
        ).all()
    return [dict(row._mapping) for row in rows]


@router.get("/runs/recent-summary")
async def recent_backtest_summary(days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT COUNT(*) AS run_count, AVG(total_return) AS avg_return, MAX(total_return) AS best_return, "
                "AVG(sharpe_ratio) AS avg_sharpe, AVG(max_drawdown) AS avg_drawdown "
                "FROM backtest_results WHERE created_at >= :since",
                {"since": since},
            )
        ).first()
    return dict(row._mapping) if row else {}
