from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.database import db_manager, execute_raw_sql
from backend.signal.explainer import SignalExplainer

logger = logging.getLogger(__name__)


@dataclass
class SignalDecision:
    signal: str
    strength: float
    confidence: float
    risk_level: str
    entry_price: float
    target_price: float
    stop_loss: float
    risk_reward: float
    explanation: str
    factors: dict[str, Any]


class SignalGenerator:
    def __init__(self) -> None:
        self.cooldown_minutes = int(os.getenv("SIGNAL_COOLDOWN_MINUTES", "30"))
        self.explainer = SignalExplainer()

    async def generate_signal(self, coin: str) -> dict[str, Any]:
        symbol = coin.upper().replace("/USDT", "").replace("USDT", "")
        data = await self._load_latest_data(symbol)
        if not data or data.get("current_price", 0.0) <= 0:
            return {
                "symbol": symbol,
                "signal": "HOLD",
                "confidence": 0.0,
                "strength": 0.0,
                "reason": "Insufficient market data",
            }

        buy_ok, buy_conditions = self.check_buy_conditions(data)
        sell_ok, sell_conditions = self.check_sell_conditions(data)

        if buy_ok and not sell_ok:
            signal_type = "BUY"
            conditions = buy_conditions
        elif sell_ok and not buy_ok:
            signal_type = "SELL"
            conditions = sell_conditions
        else:
            signal_type = "HOLD"
            conditions = {"primary": [], "supporting": [], "risk_filters": []}

        strength = self.calculate_signal_strength(conditions, data)
        targets = self.calculate_price_targets(float(data["current_price"]), signal_type, data)
        confidence = min(1.0, max(0.0, round(strength / 10.0, 4)))
        risk_level = self._determine_risk_level(data, confidence)

        decision = SignalDecision(
            signal=signal_type,
            strength=strength,
            confidence=confidence,
            risk_level=risk_level,
            entry_price=float(data["current_price"]),
            target_price=targets["target_price"],
            stop_loss=targets["stop_loss"],
            risk_reward=targets["risk_reward"],
            explanation=self.generate_explanation(signal_type, data, conditions),
            factors={
                "sentiment": data.get("sentiment_24h", 0.0),
                "prediction_confidence": data.get("prediction_confidence", 0.0),
                "onchain_score": data.get("onchain_score", 0.0),
                "conditions": conditions,
            },
        )

        signal_payload = {
            "symbol": symbol,
            "interval": "1h",
            "signal": decision.signal,
            "strength": decision.strength,
            "confidence": decision.confidence,
            "entry_price": decision.entry_price,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.target_price,
            "horizon_minutes": 240,
            "factors": decision.factors,
            "rationale": decision.explanation,
            "is_active": decision.signal in {"BUY", "SELL"},
            "expires_at": datetime.now(UTC) + timedelta(hours=4),
            "metadata": {
                "risk_level": decision.risk_level,
                "risk_reward": decision.risk_reward,
            },
            "ts": datetime.now(UTC),
            "created_at": datetime.now(UTC),
        }

        if await self._is_duplicate_or_cooldown(symbol, decision.signal):
            signal_payload["signal"] = "HOLD"
            signal_payload["is_active"] = False
            signal_payload["rationale"] = f"Suppressed by cooldown/duplicate prevention. {decision.explanation}"

        await self.save_signal(signal_payload)
        return signal_payload

    def check_buy_conditions(self, data: dict[str, Any]) -> tuple[bool, dict[str, list[str]]]:
        primary = []
        supporting = []
        risk_filters = []

        if float(data.get("sentiment_24h", 0.0)) > 0.70:
            primary.append("sentiment_gt_0.70")
        if data.get("prediction_label") == "UP":
            primary.append("prediction_up")
        if float(data.get("prediction_confidence", 0.0)) > 0.75:
            primary.append("prediction_conf_gt_0.75")

        if float(data.get("net_exchange_flow", 0.0)) < 0:
            supporting.append("whale_accumulation_net_outflow")
        if float(data.get("rsi_14", 50.0)) < 40:
            supporting.append("rsi_oversold")
        if float(data.get("volume_ratio_24h", 1.0)) > 1.0:
            supporting.append("volume_above_avg")
        if float(data.get("sentiment_velocity", 0.0)) > 0:
            supporting.append("sentiment_velocity_positive")
        if float(data.get("news_sentiment_delta", 0.0)) > 0:
            supporting.append("news_sentiment_improving")

        if float(data.get("drop_1h_pct", 0.0)) < -0.10:
            risk_filters.append("sharp_drop_gt_10pct")
        if float(data.get("atr_ratio", 1.0)) > 2.0:
            risk_filters.append("extreme_volatility")
        if bool(data.get("negative_exchange_news", False)):
            risk_filters.append("negative_exchange_news")
        if bool(data.get("regulatory_fud", False)):
            risk_filters.append("regulatory_fud")

        primary_ok = len(primary) == 3
        supporting_ok = len(supporting) >= 2
        risk_ok = len(risk_filters) == 0
        return primary_ok and supporting_ok and risk_ok, {
            "primary": primary,
            "supporting": supporting,
            "risk_filters": risk_filters,
        }

    def check_sell_conditions(self, data: dict[str, Any]) -> tuple[bool, dict[str, list[str]]]:
        primary = []
        supporting = []
        risk_filters = []

        if float(data.get("sentiment_24h", 0.0)) < 0.30:
            primary.append("sentiment_lt_0.30")
        if data.get("prediction_label") == "DOWN":
            primary.append("prediction_down")

        if float(data.get("net_exchange_flow", 0.0)) > 0:
            supporting.append("whale_distribution_net_inflow")
        if float(data.get("rsi_14", 50.0)) > 70:
            supporting.append("rsi_overbought")
        if float(data.get("volume_ratio_24h", 1.0)) > 1.3:
            supporting.append("volume_spike")
        if float(data.get("sentiment_velocity", 0.0)) < 0:
            supporting.append("sentiment_velocity_negative")
        if float(data.get("news_sentiment_delta", 0.0)) < 0:
            supporting.append("news_sentiment_deteriorating")

        if bool(data.get("hack_exploit", False)):
            risk_filters.append("hack_or_exploit")
        if bool(data.get("regulatory_fud", False)):
            risk_filters.append("regulatory_fud")

        primary_ok = len(primary) >= 2
        supporting_ok = len(supporting) >= 2
        return primary_ok and supporting_ok, {
            "primary": primary,
            "supporting": supporting,
            "risk_filters": risk_filters,
        }

    def calculate_signal_strength(self, conditions: dict[str, list[str]], data: dict[str, Any]) -> float:
        sentiment = max(0.0, min(1.0, float(data.get("sentiment_24h", 0.0))))
        prediction_conf = max(0.0, min(1.0, float(data.get("prediction_confidence", 0.0))))
        onchain_score = max(0.0, min(1.0, float(data.get("onchain_score", 0.0))))

        base = (sentiment * 0.3) + (prediction_conf * 0.4) + (onchain_score * 0.3)
        support_factor = len(conditions.get("supporting", [])) / 5.0
        score = base * max(0.2, support_factor) * 10.0
        return round(min(score, 10.0), 4)

    def calculate_price_targets(self, current_price: float, signal_type: str, data: dict[str, Any]) -> dict[str, float]:
        predicted = float(data.get("predicted_price", 0.0) or 0.0)

        if signal_type == "BUY":
            target = predicted if predicted > 0 else current_price * 1.02
            stop = current_price * 0.985
            support = float(data.get("technical_support", 0.0) or 0.0)
            if support > 0:
                stop = min(stop, support)
        elif signal_type == "SELL":
            target = predicted if predicted > 0 else current_price * 0.98
            stop = current_price * 1.015
            resistance = float(data.get("technical_resistance", 0.0) or 0.0)
            if resistance > 0:
                stop = max(stop, resistance)
        else:
            target = current_price
            stop = current_price

        risk = abs(current_price - stop)
        reward = abs(target - current_price)
        rr = reward / risk if risk > 0 else 0.0
        return {
            "target_price": round(target, 8),
            "stop_loss": round(stop, 8),
            "risk_reward": round(rr, 4),
        }

    def generate_explanation(self, signal: str, data: dict[str, Any], conditions: dict[str, list[str]]) -> str:
        return self.explainer.explain_signal(
            {
                "signal": signal,
                "symbol": data.get("symbol", "N/A"),
                "confidence": float(data.get("prediction_confidence", 0.0)),
                "strength": self.calculate_signal_strength(conditions, data),
                "entry_price": float(data.get("current_price", 0.0)),
                "target_price": float(data.get("predicted_price", data.get("current_price", 0.0))),
                "stop_loss": float(data.get("technical_support", data.get("current_price", 0.0))),
                "risk_level": self._determine_risk_level(data, float(data.get("prediction_confidence", 0.0))),
                "timeframe": "1-4 hours",
            },
            data,
            conditions,
        )

    async def save_signal(self, signal: dict[str, Any]) -> None:
        async with db_manager.session_factory() as session:
            await execute_raw_sql(
                session,
                "INSERT INTO trading_signals "
                "(ts, symbol, interval, signal, strength, confidence, entry_price, stop_loss, take_profit, "
                "horizon_minutes, factors, rationale, is_active, expires_at, metadata, created_at) "
                "VALUES (:ts, :symbol, :interval, :signal, :strength, :confidence, :entry_price, :stop_loss, :take_profit, "
                ":horizon_minutes, CAST(:factors AS jsonb), :rationale, :is_active, :expires_at, CAST(:metadata AS jsonb), :created_at)",
                {
                    **signal,
                    "factors": json.dumps(signal.get("factors", {})),
                    "metadata": json.dumps(signal.get("metadata", {})),
                },
            )
            await session.commit()

        try:
            await db_manager.redis_client.publish("signals:realtime", json.dumps(signal, default=str))
            await db_manager.redis_client.set(
                f"signals:last:{signal['symbol']}",
                json.dumps(signal, default=str),
                ex=3600,
            )
        except Exception as exc:
            logger.warning("Redis signal publish/cache failed: %s", exc)

    async def _load_latest_data(self, coin: str) -> dict[str, Any]:
        symbol = f"{coin.upper()}USDT"
        now = datetime.now(UTC)
        start_24h = now - timedelta(hours=24)

        async with db_manager.session_factory() as session:
            price_row = (
                await execute_raw_sql(
                    session,
                    "SELECT close AS current_price, volume, ts FROM price_data "
                    "WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                    {"symbol": symbol},
                )
            ).first()

            sentiment_row = (
                await execute_raw_sql(
                    session,
                    "SELECT avg_sentiment, weighted_score, ts FROM sentiment_aggregates "
                    "WHERE symbol = :coin AND window = '24h' ORDER BY ts DESC LIMIT 1",
                    {"coin": coin.upper()},
                )
            ).first()

            prediction_row = (
                await execute_raw_sql(
                    session,
                    "SELECT predicted_price, confidence, prediction_horizon, actual_price "
                    "FROM price_predictions WHERE symbol = :symbol ORDER BY ts DESC LIMIT 1",
                    {"symbol": symbol},
                )
            ).first()

            ta_row = (
                await execute_raw_sql(
                    session,
                    "SELECT rsi_14, atr_14, bb_upper, bb_lower, vwap FROM technical_indicators "
                    "WHERE symbol = :symbol AND interval = '15m' ORDER BY ts DESC LIMIT 1",
                    {"symbol": symbol},
                )
            ).first()

            onchain_row = (
                await execute_raw_sql(
                    session,
                    "SELECT "
                    "COALESCE(SUM(CASE WHEN flow_direction = 'to_exchange' THEN amount_usd ELSE 0 END),0) - "
                    "COALESCE(SUM(CASE WHEN flow_direction = 'from_exchange' THEN amount_usd ELSE 0 END),0) AS net_exchange_flow, "
                    "COUNT(*) FILTER (WHERE is_whale = true) AS whale_count "
                    "FROM onchain_transactions WHERE symbol = :coin AND ts >= :start_24h",
                    {"coin": coin.upper(), "start_24h": start_24h},
                )
            ).first()

            change_row = (
                await execute_raw_sql(
                    session,
                    "SELECT ((MAX(close) - MIN(close))/NULLIF(MIN(close),0)) AS pct_move_1h "
                    "FROM price_data WHERE symbol = :symbol AND interval = '15m' AND ts >= :start_1h",
                    {"symbol": symbol, "start_1h": now - timedelta(hours=1)},
                )
            ).first()

        current_price = float(price_row.current_price) if price_row and price_row.current_price else 0.0
        predicted_price = float(prediction_row.predicted_price) if prediction_row and prediction_row.predicted_price else 0.0
        direction = "UP" if predicted_price > current_price else "DOWN" if predicted_price < current_price else "SIDEWAYS"
        volume_ratio = 1.0
        if price_row and price_row.volume:
            volume_ratio = 1.0

        sentiment_current = float(sentiment_row.avg_sentiment or 0.5) if sentiment_row else 0.5
        sentiment_weighted = float(sentiment_row.weighted_score or sentiment_current) if sentiment_row else sentiment_current

        return {
            "symbol": coin.upper(),
            "current_price": current_price,
            "sentiment_24h": sentiment_current,
            "sentiment_velocity": sentiment_weighted - sentiment_current,
            "prediction_label": direction,
            "prediction_confidence": float(prediction_row.confidence or 0.5) if prediction_row else 0.5,
            "predicted_price": predicted_price,
            "rsi_14": float(ta_row.rsi_14 or 50.0) if ta_row else 50.0,
            "atr_ratio": float(ta_row.atr_14 or 1.0) if ta_row else 1.0,
            "volume_ratio_24h": volume_ratio,
            "net_exchange_flow": float(onchain_row.net_exchange_flow or 0.0) if onchain_row else 0.0,
            "onchain_score": min(1.0, (float(onchain_row.whale_count or 0) / 10.0)) if onchain_row else 0.0,
            "drop_1h_pct": float(change_row.pct_move_1h or 0.0) if change_row else 0.0,
            "news_sentiment_delta": 0.0,
            "negative_exchange_news": False,
            "regulatory_fud": False,
            "hack_exploit": False,
            "technical_support": float(ta_row.bb_lower or 0.0) if ta_row else 0.0,
            "technical_resistance": float(ta_row.bb_upper or 0.0) if ta_row else 0.0,
        }

    async def _is_duplicate_or_cooldown(self, symbol: str, signal: str) -> bool:
        if signal not in {"BUY", "SELL"}:
            return False

        async with db_manager.session_factory() as session:
            row = (
                await execute_raw_sql(
                    session,
                    "SELECT signal, ts FROM trading_signals "
                    "WHERE symbol = :symbol ORDER BY ts DESC LIMIT 1",
                    {"symbol": symbol},
                )
            ).first()

        if not row:
            return False

        last_ts = row.ts if isinstance(row.ts, datetime) else None
        if row.signal == signal:
            return True
        if last_ts and (datetime.now(UTC) - last_ts) < timedelta(minutes=self.cooldown_minutes):
            return True
        return False

    def _determine_risk_level(self, data: dict[str, Any], confidence: float) -> str:
        atr = float(data.get("atr_ratio", 1.0))
        if atr > 1.8 or confidence < 0.55:
            return "HIGH"
        if atr > 1.2 or confidence < 0.75:
            return "MEDIUM"
        return "LOW"


async def generate_signal_async(symbol: str) -> dict[str, Any]:
    return await SignalGenerator().generate_signal(symbol)


def generate_signal(symbol: str) -> dict[str, Any]:
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return {
                "symbol": symbol,
                "signal": "HOLD",
                "confidence": 0.0,
                "strength": 0.0,
                "reason": "Use generate_signal_async inside running event loop",
            }
    except RuntimeError:
        pass
    return asyncio.run(generate_signal_async(symbol))
