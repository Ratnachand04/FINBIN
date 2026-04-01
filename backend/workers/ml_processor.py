import time
from datetime import datetime

from backend.ml.features import build_feature_vector
from backend.ml.inference import predict_signal
from backend.models.market import MarketTick


def run() -> None:
    while True:
        tick = MarketTick(symbol="BTCUSDT", price=100.0, volume=1.0, ts=datetime.utcnow())
        features = build_feature_vector(tick)
        signal, confidence = predict_signal(features)
        print(f"ml_processor signal={signal} confidence={confidence}")
        time.sleep(15)


if __name__ == "__main__":
    run()
