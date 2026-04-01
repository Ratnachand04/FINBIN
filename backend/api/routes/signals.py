from fastapi import APIRouter

from backend.signal.generator import generate_signal

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/{symbol}")
def get_signal(symbol: str) -> dict:
    return generate_signal(symbol)
