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
            now = datetime.now(UTC).isoformat()
            for symbol, price in (("BTCUSDT", 70000.0), ("ETHUSDT", 3500.0), ("DOGEUSDT", 0.25)):
                message = {
                    "type": "ticker",
                    "symbol": symbol,
                    "price": price,
                    "timestamp": now,
                }
                await websocket.send_text(json.dumps(message))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
