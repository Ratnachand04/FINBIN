from datetime import datetime

from backend.ml.features import build_feature_vector
from backend.models.market import MarketTick


def test_build_feature_vector():
    tick = MarketTick(symbol="BTCUSDT", price=10.0, volume=2.0, ts=datetime.utcnow())
    features = build_feature_vector(tick)
    assert features == [10.0, 2.0]
