from fastapi import APIRouter

from backend.signal.generator import generate_signal_async

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/{symbol}")
async def get_signal(symbol: str) -> dict:
    return await generate_signal_async(symbol)
