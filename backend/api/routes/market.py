from datetime import datetime

from fastapi import APIRouter

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/snapshot")
def market_snapshot() -> dict[str, str]:
    return {"timestamp": datetime.utcnow().isoformat(), "note": "Market snapshot placeholder"}
