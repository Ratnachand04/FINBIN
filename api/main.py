from __future__ import annotations

from fastapi import FastAPI

from api.routes.signals import router as signals_router
from api.websocket import router as websocket_router

app = FastAPI(title="BINFIN API", version="1.0.0")
app.include_router(signals_router, prefix="/api/v1")
app.include_router(websocket_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
