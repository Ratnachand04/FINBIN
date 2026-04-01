from __future__ import annotations

from datetime import datetime
from typing import Any


class SignalExplainer:
    def explain_signal(self, signal: dict[str, Any], data: dict[str, Any], conditions: dict[str, list[str]]) -> str:
        icon = "🟢" if signal.get("signal") == "BUY" else "🔴" if signal.get("signal") == "SELL" else "🟡"
        title = f"{icon} {signal.get('signal', 'HOLD')} - {signal.get('symbol', 'N/A')}/USDT"

        primary = "\n".join([f"• {item}" for item in conditions.get("primary", [])]) or "• No strong primary factors"
        support = "\n".join([f"✓ {item}" for item in conditions.get("supporting", [])]) or "✓ Limited supporting evidence"
        warnings = self.generate_risk_warnings(signal, data)
        warning_text = "\n".join([f"⚠ {item}" for item in warnings]) if warnings else "⚠ No immediate warnings"

        confidence_pct = round(float(signal.get("confidence", 0.0)) * 100, 1)
        strength = float(signal.get("strength", 0.0))
        entry = float(signal.get("entry_price", 0.0))
        target = float(signal.get("target_price", signal.get("take_profit", 0.0) or 0.0))
        stop = float(signal.get("stop_loss", 0.0))
        rr = self._risk_reward(entry, target, stop)

        return (
            f"{title}\n\n"
            f"Confidence: {confidence_pct}%\n"
            f"Signal Strength: {strength:.2f}/10\n\n"
            "Primary Factors:\n"
            f"{primary}\n\n"
            "Supporting Evidence:\n"
            f"{support}\n\n"
            "Technical Levels:\n"
            f"• Entry: ${entry:,.4f}\n"
            f"• Target: ${target:,.4f}\n"
            f"• Stop Loss: ${stop:,.4f}\n"
            f"• Risk/Reward: 1:{rr:.2f}\n\n"
            f"Timeframe: {signal.get('timeframe', '1-4 hours')}\n"
            f"Risk Level: {signal.get('risk_level', 'MEDIUM')}\n\n"
            "Risk Warnings:\n"
            f"{warning_text}"
        )

    def create_factor_breakdown(self, signal: dict[str, Any]) -> dict[str, Any]:
        factors = signal.get("factors", {}) if isinstance(signal.get("factors"), dict) else {}
        return {
            "labels": ["Sentiment", "Prediction", "On-chain"],
            "values": [30, 40, 30],
            "technical_overlay": {
                "rsi": factors.get("rsi_14"),
                "atr": factors.get("atr_ratio"),
            },
        }

    def extract_key_insights(self, data: dict[str, Any]) -> list[str]:
        insights: list[tuple[str, float]] = [
            ("Sentiment strength", abs(float(data.get("sentiment_24h", 0.0) - 0.5))),
            ("Prediction confidence", float(data.get("prediction_confidence", 0.0))),
            ("On-chain flow intensity", abs(float(data.get("net_exchange_flow", 0.0)))),
            ("RSI dislocation", abs(float(data.get("rsi_14", 50.0) - 50.0))),
            ("Volatility pressure", float(data.get("atr_ratio", 1.0))),
        ]
        ordered = sorted(insights, key=lambda item: item[1], reverse=True)[:5]
        return [f"{name}: {value:.4f}" for name, value in ordered]

    def generate_risk_warnings(self, signal: dict[str, Any], data: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        if float(data.get("atr_ratio", 1.0)) > 1.8:
            warnings.append("Elevated volatility may trigger stop-loss quickly")
        if float(data.get("drop_1h_pct", 0.0)) < -0.08:
            warnings.append("Recent downside momentum remains significant")
        if bool(data.get("negative_exchange_news", False)):
            warnings.append("Negative exchange news may impact liquidity")
        if bool(data.get("regulatory_fud", False)):
            warnings.append("Regulatory uncertainty can amplify drawdowns")
        if signal.get("risk_level") == "HIGH":
            warnings.append("High-risk setup: reduce position size")
        return warnings

    def format_for_notification(self, signal: dict[str, Any]) -> str:
        icon = "🟢" if signal.get("signal") == "BUY" else "🔴" if signal.get("signal") == "SELL" else "🟡"
        symbol = str(signal.get("symbol", "N/A"))
        entry = float(signal.get("entry_price", 0.0))
        target = float(signal.get("target_price", signal.get("take_profit", 0.0) or 0.0))
        confidence_pct = round(float(signal.get("confidence", 0.0)) * 100)
        return f"{icon} {symbol} {signal.get('signal','HOLD')} @ ${entry:,.2f} | Target ${target:,.2f} | Conf: {confidence_pct}%"

    def create_comparison_table(
        self,
        current_signal: dict[str, Any],
        historical_signals: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not historical_signals:
            return {
                "headers": ["Metric", "Value"],
                "rows": [["Similar setups", "0"], ["Success rate", "N/A"], ["Avg outcome", "N/A"]],
            }

        current_type = current_signal.get("signal")
        peers = [row for row in historical_signals if row.get("signal") == current_type]
        success = [row for row in peers if float(row.get("pnl", 0.0)) > 0]
        avg_outcome = sum(float(row.get("pnl", 0.0)) for row in peers) / len(peers) if peers else 0.0
        success_rate = (len(success) / len(peers) * 100) if peers else 0.0

        return {
            "headers": ["Metric", "Value"],
            "rows": [
                ["Similar setups", str(len(peers))],
                ["Success rate", f"{success_rate:.2f}%"],
                ["Average outcome", f"{avg_outcome:.4f}"],
                ["Last compared", datetime.utcnow().isoformat()],
            ],
        }

    def _risk_reward(self, entry: float, target: float, stop: float) -> float:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        return reward / risk if risk > 0 else 0.0
