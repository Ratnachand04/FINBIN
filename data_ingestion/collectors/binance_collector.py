from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


class OHLCVTick(BaseModel):
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: float = Field(ge=0)


class BinanceCollector:
    def __init__(self, symbols: list[str] | None = None, interval: str = "15m") -> None:
        self.symbols = symbols or ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
        self.interval = interval
        self._stop = asyncio.Event()

    async def stream_ohlcv(self) -> AsyncGenerator[OHLCVTick, None]:
        try:
            import websockets  # type: ignore
        except Exception as exc:
            logger.warning("websockets package unavailable: %s", exc)
            return

        stream = "/".join([f"{symbol.lower()}@kline_{self.interval}" for symbol in self.symbols])
        url = f"wss://stream.binance.com:9443/stream?streams={stream}"
        reconnect_backoff = 1

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    reconnect_backoff = 1
                    while not self._stop.is_set():
                        raw = await ws.recv()
                        payload = json.loads(raw)
                        data = payload.get("data", {})
                        kline = data.get("k", {})
                        try:
                            tick = OHLCVTick(
                                symbol=str(kline.get("s")),
                                interval=str(kline.get("i")),
                                open_time=datetime.fromtimestamp(int(kline.get("t", 0)) / 1000, tz=UTC),
                                close_time=datetime.fromtimestamp(int(kline.get("T", 0)) / 1000, tz=UTC),
                                open=float(kline.get("o", 0.0)),
                                high=float(kline.get("h", 0.0)),
                                low=float(kline.get("l", 0.0)),
                                close=float(kline.get("c", 0.0)),
                                volume=float(kline.get("v", 0.0)),
                            )
                        except (ValidationError, ValueError) as exc:
                            logger.debug("invalid kline payload skipped: %s", exc)
                            continue
                        yield tick
            except Exception as exc:
                logger.warning("binance websocket disconnected: %s", exc)
                await asyncio.sleep(reconnect_backoff)
                reconnect_backoff = min(reconnect_backoff * 2, 30)

    def stop(self) -> None:
        self._stop.set()
