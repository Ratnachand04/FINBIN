from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from signals.risk_manager import RiskManager


@dataclass
class MarketSnapshot:
    symbol: str
    price: float
    prediction: float
    sentiment: float
    volatility: float
    confidence: float


@dataclass
class TradeSignal:
    symbol: str
    side: str
    confidence: float
    strength: float
    entry: float
    stop_loss: float
    take_profit: float
    reason: str
    timestamp: datetime


class SignalGenerator:
    def __init__(self) -> None:
        self.risk = RiskManager()
        self.cooldown_map: dict[str, datetime] = {}
        self.cooldown_minutes = 15

    def generate(self, snapshot: MarketSnapshot) -> TradeSignal | None:
        now = datetime.now(UTC)
        last = self.cooldown_map.get(snapshot.symbol)
        if last and now - last < timedelta(minutes=self.cooldown_minutes):
            return None

        move_pct = (snapshot.prediction - snapshot.price) / snapshot.price if snapshot.price else 0
        directional_score = move_pct * 2
        sentiment_score = (snapshot.sentiment - 0.5) * 1.5
        strength = directional_score + sentiment_score

        side = "HOLD"
        if strength > 0.15:
            side = "BUY"
        elif strength < -0.15:
            side = "SELL"

        if side == "HOLD":
            return None

        confidence = self.risk.adjust_confidence(snapshot.confidence, snapshot.volatility)
        levels = self.risk.build_levels(snapshot.price, side)
        self.cooldown_map[snapshot.symbol] = now

        return TradeSignal(
            symbol=snapshot.symbol,
            side=side,
            confidence=confidence,
            strength=abs(strength),
            entry=snapshot.price,
            stop_loss=levels["stop_loss"],
            take_profit=levels["take_profit"],
            reason=f"pred_move={move_pct:.4f}, sentiment={snapshot.sentiment:.3f}",
            timestamp=now,
        )
