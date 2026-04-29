from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from backend.api.backtest import router as backtest_router
from backend.api.coins import router as coins_router
from backend.api.health import router as health_v1_router
from backend.api.live_feed import router as live_feed_router
from backend.api.llm import router as llm_router
from backend.api.model_ops import router as model_ops_router
from backend.api.predictions import router as predictions_router
from backend.api.routes.health import router as legacy_health_router
from backend.api.routes.market import router as legacy_market_router
from backend.api.routes.signals import router as legacy_signals_router
from backend.api.sentiment import router as sentiment_router
from backend.api.signals import router as signals_router
from backend.config import settings
from backend.database import db_manager, db_session_context, execute_raw_sql
from backend.workers.finetune_worker import celery_app  # noqa: F401

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next: Any) -> JSONResponse:
		request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
		start = time.perf_counter()
		response = await call_next(request)
		elapsed_ms = (time.perf_counter() - start) * 1000
		response.headers["X-Request-ID"] = request_id
		response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.2f}"
		return response


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
	def __init__(self, app: Any, per_minute: int = 180) -> None:
		super().__init__(app)
		self.per_minute = per_minute
		self._bucket: dict[str, list[float]] = {}

	async def dispatch(self, request: Request, call_next: Any) -> JSONResponse:
		client_ip = request.client.host if request.client else "unknown"
		now = time.time()
		window_start = now - 60
		entries = [ts for ts in self._bucket.get(client_ip, []) if ts >= window_start]
		if len(entries) >= self.per_minute:
			return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
		entries.append(now)
		self._bucket[client_ip] = entries
		return await call_next(request)


@asynccontextmanager
async def lifespan(_: FastAPI):
	await db_manager.initialize()
	app.state.started_at = datetime.now(UTC)
	app.state.ws_clients = set()
	logger.info("Application startup complete")
	try:
		yield
	finally:
		for ws in list(app.state.ws_clients):
			try:
				await ws.close()
			except Exception:
				pass
		await db_manager.close()
		logger.info("Application shutdown complete")


app = FastAPI(
	title=settings.app_name,
	description="BINFIN crypto trading intelligence API",
	version="1.0.0",
	lifespan=lifespan,
	docs_url="/docs",
	redoc_url="/redoc",
	openapi_url="/openapi.json",
)

app.add_middleware(
	CORSMiddleware,
	allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(SimpleRateLimitMiddleware, per_minute=int(os.getenv("RATE_LIMIT_PER_MIN", "180")))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
	return JSONResponse(status_code=422, content={"detail": exc.errors(), "message": "Invalid request payload"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
	logger.exception("Unhandled application error: %s", exc)
	return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/")
async def root() -> dict[str, Any]:
	return {
		"name": settings.app_name,
		"status": "ok",
		"environment": settings.env,
		"timestamp": datetime.now(UTC).isoformat(),
	}


@app.get("/api/v1/system")
async def system_info() -> dict[str, Any]:
	return {
		"env": settings.env,
		"log_level": settings.log_level,
		"started_at": app.state.started_at.isoformat() if hasattr(app.state, "started_at") else None,
	}


@app.get("/metrics")
async def metrics() -> Response:
	return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _tracked_pairs_from_env() -> list[str]:
	raw = os.getenv("TRACKED_COINS", "BTC,ETH,DOGE")
	pairs = [f"{item.strip().upper()}USDT" for item in raw.split(",") if item.strip()]
	return pairs or ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]


async def _tracked_pairs(interval: str) -> list[str]:
	try:
		max_symbols = max(1, int(os.getenv("LIVE_DASHBOARD_MAX_SYMBOLS", "60")))
	except ValueError:
		max_symbols = 60
	env_pairs = _tracked_pairs_from_env()
	ranked_pairs: list[str] = []
	seen: set[str] = set()

	def _append_pair(raw_symbol: Any) -> None:
		if raw_symbol is None:
			return
		symbol = str(raw_symbol).strip().upper()
		if not symbol:
			return
		pair = symbol if symbol.endswith("USDT") else f"{symbol}USDT"
		if pair in seen:
			return
		seen.add(pair)
		ranked_pairs.append(pair)

	try:
		async with db_session_context() as session:
			config_rows = (
				await execute_raw_sql(
					session,
					"SELECT symbol FROM coin_configs WHERE is_enabled = true ORDER BY symbol ASC LIMIT :limit",
					{"limit": max_symbols},
				)
			).all()

			recent_rows = (
				await execute_raw_sql(
					session,
					"SELECT symbol "
					"FROM price_data "
					"WHERE interval = :interval "
					"AND symbol LIKE '%USDT' "
					"AND ts >= NOW() - INTERVAL '48 hours' "
					"GROUP BY symbol "
					"ORDER BY MAX(ts) DESC "
					"LIMIT :limit",
					{"interval": interval, "limit": max_symbols},
				)
			).all()

		for row in config_rows:
			_append_pair(row.symbol)
		for row in recent_rows:
			_append_pair(row.symbol)
	except Exception as exc:
		logger.warning("tracked pair discovery failed, using env fallback: %s", exc)

	for pair in env_pairs:
		_append_pair(pair)

	return ranked_pairs or env_pairs


async def _build_market_snapshot() -> dict[str, Any]:
	interval = os.getenv("LIVE_FEED_INTERVAL", "15m")
	tracked_pairs = await _tracked_pairs(interval)

	price_sql = (
		"SELECT DISTINCT ON (symbol) symbol, close, ts "
		"FROM price_data "
		"WHERE symbol = ANY(CAST(:symbols AS text[])) AND interval = :interval "
		"ORDER BY symbol, ts DESC"
	)
	signal_sql = (
		"SELECT symbol, signal, confidence, ts "
		"FROM trading_signals "
		"WHERE is_active = true AND symbol = ANY(CAST(:symbols AS text[])) "
		"ORDER BY ts DESC "
		"LIMIT 20"
	)

	async with db_session_context() as session:
		try:
			price_rows = (await execute_raw_sql(session, price_sql, {"symbols": tracked_pairs, "interval": interval})).all()
		except Exception as exc:
			logger.warning("price snapshot query failed: %s", exc)
			price_rows = []

		try:
			signal_rows = (await execute_raw_sql(session, signal_sql, {"symbols": tracked_pairs})).all()
		except Exception as exc:
			logger.warning("signal snapshot query failed: %s", exc)
			signal_rows = []

	prices: list[dict[str, Any]] = []
	for row in price_rows:
		prices.append(
			{
				"symbol": str(row.symbol),
				"coin": str(row.symbol).replace("USDT", ""),
				"price": float(row.close),
				"ts": row.ts.isoformat() if row.ts else None,
			}
		)

	active_signals: list[dict[str, Any]] = []
	for row in signal_rows:
		active_signals.append(
			{
				"symbol": str(row.symbol),
				"signal": str(row.signal).upper(),
				"confidence": float(row.confidence or 0.0),
				"ts": row.ts.isoformat() if row.ts else None,
			}
		)

	return {
		"type": "market_snapshot",
		"ts": datetime.now(UTC).isoformat(),
		"interval": interval,
		"symbols": prices,
		"active_signals": active_signals,
	}


@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket) -> None:
	await websocket.accept()
	app.state.ws_clients.add(websocket)
	try:
		while True:
			try:
				snapshot = await _build_market_snapshot()
				await websocket.send_json(snapshot)
			except Exception as exc:
				logger.warning("live websocket snapshot failed: %s", exc)
				await websocket.send_json({"type": "heartbeat", "ts": datetime.now(UTC).isoformat()})
			await asyncio.sleep(3)
	except WebSocketDisconnect:
		pass
	finally:
		app.state.ws_clients.discard(websocket)


# Legacy routers retained for backward compatibility.
app.include_router(legacy_health_router, prefix="/api")
app.include_router(legacy_market_router, prefix="/api")
app.include_router(legacy_signals_router, prefix="/api")

# New API v1 routers.
app.include_router(health_v1_router)
app.include_router(coins_router)
app.include_router(live_feed_router)
app.include_router(signals_router)
app.include_router(sentiment_router)
app.include_router(predictions_router)
app.include_router(backtest_router)
app.include_router(model_ops_router)
app.include_router(llm_router)

from backend.api.rag import router as rag_router
app.include_router(rag_router)

import os
static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")
if os.path.exists(static_dir):
	app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
