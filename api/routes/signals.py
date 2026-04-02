from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/signals", tags=["signals"])


class SignalResponse(BaseModel):
    symbol: str
    side: str
    confidence: float
    strength: float


@router.get("/latest", response_model=list[SignalResponse])
async def latest_signals() -> list[SignalResponse]:
    return [
        SignalResponse(symbol="BTCUSDT", side="BUY", confidence=0.79, strength=0.65),
        SignalResponse(symbol="ETHUSDT", side="BUY", confidence=0.73, strength=0.61),
        SignalResponse(symbol="DOGEUSDT", side="HOLD", confidence=0.66, strength=0.58),
    ]
