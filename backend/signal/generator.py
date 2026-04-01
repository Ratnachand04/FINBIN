from backend.collectors.market_collector import collect_market_tick
from backend.ml.features import build_feature_vector
from backend.ml.inference import predict_signal


def generate_signal(symbol: str) -> dict:
    tick = collect_market_tick(symbol)
    features = build_feature_vector(tick)
    signal, confidence = predict_signal(features)
    return {
        "symbol": symbol,
        "signal": signal,
        "confidence": confidence,
        "price": tick.price,
    }
