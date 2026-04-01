from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            message = {
                "type": "ticker",
                "symbol": "BTCUSDT",
                "price": 70000.0,
                "timestamp": datetime.now(UTC).isoformat(),
            }
            await websocket.send_text(json.dumps(message))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
