from backend.models.market import MarketTick


def build_feature_vector(tick: MarketTick) -> list[float]:
    return [tick.price, tick.volume]
