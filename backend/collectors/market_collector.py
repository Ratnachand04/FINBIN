from datetime import datetime

from backend.models.market import MarketTick


def collect_market_tick(symbol: str) -> MarketTick:
    return MarketTick(symbol=symbol, price=100.0, volume=1.0, ts=datetime.utcnow())
