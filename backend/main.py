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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from backend.api.backtest import router as backtest_router
from backend.api.coins import router as coins_router
from backend.api.health import router as health_v1_router
from backend.api.llm import router as llm_router
from backend.api.model_ops import router as model_ops_router
from backend.api.predictions import router as predictions_router
from backend.api.routes.health import router as legacy_health_router
from backend.api.routes.market import router as legacy_market_router
from backend.api.routes.signals import router as legacy_signals_router
from backend.api.sentiment import router as sentiment_router
from backend.api.signals import router as signals_router
from backend.config import settings
from backend.database import db_manager
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


@app.websocket("/ws/live-data")
async def websocket_live_data(websocket: WebSocket) -> None:
	token = websocket.query_params.get("token")
	ws_token = os.getenv("WS_TOKEN")
	if ws_token and token != ws_token:
		await websocket.close(code=1008)
		return

	await websocket.accept()
	app.state.ws_clients.add(websocket)
	try:
		while True:
			await websocket.send_json({"type": "heartbeat", "ts": datetime.now(UTC).isoformat()})
			await asyncio.sleep(10)
	except WebSocketDisconnect:
		pass
	finally:
		app.state.ws_clients.discard(websocket)


# Legacy routers retained for backward compatibility.
app.include_router(legacy_health_router, prefix="/api")
app.include_router(legacy_market_router, prefix="/api")
app.include_router(legacy_signals_router, prefix="/api")

from backend.api.auth import router as auth_router
from backend.api.keys import router as keys_router
app.include_router(auth_router)
app.include_router(keys_router)

# New API v1 routers.
app.include_router(health_v1_router)
app.include_router(coins_router)
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
