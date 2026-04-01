def position_size(account_size: float, risk_pct: float, stop_distance: float) -> float:
    if stop_distance <= 0:
        return 0.0
    return (account_size * risk_pct) / stop_distance
