from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskProfile:
    max_position_size: float
    stop_loss_pct: float
    take_profit_pct: float
    leverage_limit: float


class RiskManager:
    def __init__(self) -> None:
        self.default = RiskProfile(max_position_size=0.10, stop_loss_pct=0.03, take_profit_pct=0.06, leverage_limit=2.0)

    def adjust_confidence(self, confidence: float, volatility: float) -> float:
        vol_penalty = min(max(volatility, 0.0), 1.0) * 0.3
        return max(min(confidence * (1 - vol_penalty), 1.0), 0.0)

    def build_levels(self, current_price: float, side: str, profile: RiskProfile | None = None) -> dict[str, float]:
        profile = profile or self.default
        if side == "BUY":
            return {
                "stop_loss": current_price * (1 - profile.stop_loss_pct),
                "take_profit": current_price * (1 + profile.take_profit_pct),
            }
        return {
            "stop_loss": current_price * (1 + profile.stop_loss_pct),
            "take_profit": current_price * (1 - profile.take_profit_pct),
        }
