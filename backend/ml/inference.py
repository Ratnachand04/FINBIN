def predict_signal(features: list[float]) -> tuple[str, float]:
    score = sum(features)
    if score > 100:
        return "BUY", 0.8
    return "HOLD", 0.6
