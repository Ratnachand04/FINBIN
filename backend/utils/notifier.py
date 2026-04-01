from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, time
from email.message import EmailMessage
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NotificationConfig:
    enabled_channels: list[str]
    min_confidence: float = 0.7
    min_strength: float = 6.0
    quiet_hours_start: int = 23
    quiet_hours_end: int = 7
    rate_limit_seconds: int = 10
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    from_address: str | None = None
    to_address: str | None = None


class Notifier:
    def __init__(self, config: dict[str, Any] | NotificationConfig | None = None) -> None:
        if isinstance(config, NotificationConfig):
            self.config = config
        else:
            raw = config or {}
            channels = raw.get("enabled_channels") or os.getenv("NOTIFIER_CHANNELS", "desktop").split(",")
            self.config = NotificationConfig(
                enabled_channels=[c.strip().lower() for c in channels if c.strip()],
                min_confidence=float(raw.get("min_confidence", os.getenv("NOTIFY_MIN_CONFIDENCE", "0.7"))),
                min_strength=float(raw.get("min_strength", os.getenv("NOTIFY_MIN_STRENGTH", "6.0"))),
                quiet_hours_start=int(raw.get("quiet_hours_start", os.getenv("QUIET_HOURS_START", "23"))),
                quiet_hours_end=int(raw.get("quiet_hours_end", os.getenv("QUIET_HOURS_END", "7"))),
                rate_limit_seconds=int(raw.get("rate_limit_seconds", os.getenv("NOTIFY_RATE_LIMIT_SECONDS", "10"))),
                telegram_token=raw.get("telegram_token") or os.getenv("TELEGRAM_BOT_TOKEN"),
                telegram_chat_id=raw.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID"),
                smtp_host=raw.get("smtp_host") or os.getenv("SMTP_HOST"),
                smtp_port=int(raw.get("smtp_port", os.getenv("SMTP_PORT", "587"))),
                smtp_user=raw.get("smtp_user") or os.getenv("SMTP_USER"),
                smtp_password=raw.get("smtp_password") or os.getenv("SMTP_PASSWORD"),
                from_address=raw.get("from_address") or os.getenv("NOTIFY_FROM_EMAIL"),
                to_address=raw.get("to_address") or os.getenv("NOTIFY_TO_EMAIL"),
            )

        self.history: deque[dict[str, Any]] = deque(maxlen=1000)
        self.templates = {
            "signal": "{icon} {label} - {symbol}\nConfidence: {confidence:.0%} | Strength: {strength:.1f}/10\n\n"
            "Entry: ${entry:,.4f}\nTarget: ${target:,.4f} ({target_pct:+.2f}%)\nStop: ${stop:,.4f} ({stop_pct:+.2f}%)\n\n"
            "Key Factors:\n{factors}",
            "system": "[{severity}] {timestamp} - {alert_type}\n{message}",
        }
        self._last_sent: datetime | None = None

    async def send_signal_alert(self, signal: dict[str, Any]) -> bool:
        if not self.check_notification_preferences(signal):
            return False

        message = self.format_signal_message(signal)
        confidence = float(signal.get("confidence") or 0.0)
        strength = float(signal.get("strength") or 0.0)
        priority = "high" if confidence >= 0.85 and strength >= 8.0 else "normal"
        sent_channels: list[str] = []

        if "desktop" in self.config.enabled_channels:
            self.send_desktop_notification(
                title=f"Trading Alert: {signal.get('signal', 'HOLD')} {signal.get('symbol', 'UNK')}",
                message=message,
                urgency=priority,
            )
            sent_channels.append("desktop")

        if "telegram" in self.config.enabled_channels and self.config.telegram_chat_id:
            ok = await self.send_telegram_message(message, self.config.telegram_chat_id)
            if ok:
                sent_channels.append("telegram")

        if "email" in self.config.enabled_channels and self.config.to_address:
            ok = await self.send_email(
                subject=f"BINFIN Alert: {signal.get('signal')} {signal.get('symbol')}",
                body=message,
                to_address=self.config.to_address,
            )
            if ok:
                sent_channels.append("email")

        self._append_history(
            {
                "type": "signal",
                "symbol": signal.get("symbol"),
                "signal": signal.get("signal"),
                "priority": priority,
                "channels": sent_channels,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        logger.info("Notification sent for %s via %s", signal.get("symbol"), sent_channels)
        return bool(sent_channels)

    def send_desktop_notification(self, title: str, message: str, urgency: str = "normal") -> None:
        try:
            from plyer import notification  # type: ignore

            timeout = 6 if urgency != "high" else 12
            app_name = "BINFIN"
            system_name = platform.system().lower()
            icon_path = None
            if "windows" in system_name:
                icon_path = None
            elif "darwin" in system_name:
                icon_path = None
            elif "linux" in system_name:
                icon_path = None

            notification.notify(
                title=title,
                message=message[:2500],
                app_name=app_name,
                timeout=timeout,
                app_icon=icon_path,
            )

            if urgency == "high":
                print("\a", end="")
        except Exception as exc:
            logger.warning("Desktop notification failed: %s", exc)

    async def send_telegram_message(self, message: str, chat_id: str) -> bool:
        if not self.config.telegram_token:
            return False

        now = datetime.now(UTC)
        if self._last_sent and (now - self._last_sent).total_seconds() < self.config.rate_limit_seconds:
            await asyncio.sleep(self.config.rate_limit_seconds)

        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore
            from telegram.constants import ParseMode  # type: ignore
            from telegram.ext import Application  # type: ignore

            app = Application.builder().token(self.config.telegram_token).build()
            keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("View Details", callback_data="view_signal")],
                    [InlineKeyboardButton("Dismiss", callback_data="dismiss_signal")],
                ]
            )
            await app.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            self._last_sent = now
            return True
        except Exception as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False

    async def send_email(self, subject: str, body: str, to_address: str) -> bool:
        if not self.config.smtp_host or not self.config.from_address:
            return False

        html_body = (
            "<html><body>"
            "<h2>BINFIN Notification</h2>"
            f"<p>{body.replace(chr(10), '<br/>')}</p>"
            "<hr/><small>This alert was generated automatically.</small>"
            "</body></html>"
        )

        msg = EmailMessage()
        msg["From"] = self.config.from_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.set_content(body)
        msg.add_alternative(html_body, subtype="html")

        try:
            import aiosmtplib  # type: ignore

            await aiosmtplib.send(
                msg,
                hostname=self.config.smtp_host,
                port=self.config.smtp_port,
                start_tls=True,
                username=self.config.smtp_user,
                password=self.config.smtp_password,
                timeout=20,
            )
            return True
        except Exception as exc:
            logger.warning("Email send failed: %s", exc)
            return False

    def format_signal_message(self, signal: dict[str, Any]) -> str:
        signal_type = str(signal.get("signal", "HOLD")).upper()
        symbol = str(signal.get("symbol", "BTC/USDT"))
        confidence = float(signal.get("confidence") or 0.0)
        strength = float(signal.get("strength") or 0.0)
        entry = float(signal.get("entry_price") or 0.0)
        target = float(signal.get("take_profit") or entry)
        stop = float(signal.get("stop_loss") or entry)

        icon_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
        label_map = {"BUY": "STRONG BUY", "SELL": "STRONG SELL", "HOLD": "HOLD"}
        icon = icon_map.get(signal_type, "🟡")
        label = label_map.get(signal_type, signal_type)

        target_pct = ((target - entry) / entry * 100) if entry else 0.0
        stop_pct = ((stop - entry) / entry * 100) if entry else 0.0

        factors_raw = signal.get("factors") or {}
        if isinstance(factors_raw, str):
            try:
                factors_raw = json.loads(factors_raw)
            except Exception:
                factors_raw = {}

        bullets: list[str] = []
        conditions = factors_raw.get("conditions") if isinstance(factors_raw, dict) else None
        if isinstance(conditions, dict):
            for key in ("primary", "supporting"):
                for item in conditions.get(key, [])[:4]:
                    bullets.append(f"• {str(item).replace('_', ' ')}")

        if not bullets:
            bullets = [
                f"• Bullish sentiment ({float(factors_raw.get('sentiment', 0.0) or 0.0):.2f})",
                f"• Prediction confidence ({float(factors_raw.get('prediction_confidence', 0.0) or 0.0):.2f})",
                "• Multi-factor validation",
            ]

        return self.templates["signal"].format(
            icon=icon,
            label=label,
            symbol=symbol,
            confidence=confidence,
            strength=strength,
            entry=entry,
            target=target,
            target_pct=target_pct,
            stop=stop,
            stop_pct=stop_pct,
            factors="\n".join(bullets),
        )

    def format_system_alert(self, alert_type: str, message: str) -> str:
        severity = "INFO"
        upper = alert_type.upper()
        if "ERROR" in upper:
            severity = "ERROR"
        elif "WARN" in upper:
            severity = "WARNING"

        return self.templates["system"].format(
            severity=severity,
            timestamp=datetime.now(UTC).isoformat(),
            alert_type=alert_type,
            message=message,
        )

    def check_notification_preferences(self, signal: dict[str, Any]) -> bool:
        now = datetime.now(UTC)
        confidence = float(signal.get("confidence") or 0.0)
        strength = float(signal.get("strength") or 0.0)

        if confidence < self.config.min_confidence or strength < self.config.min_strength:
            return False

        hour = now.hour
        start = self.config.quiet_hours_start
        end = self.config.quiet_hours_end
        in_quiet = (start <= hour <= 23) or (0 <= hour < end) if start > end else (start <= hour < end)

        if in_quiet and confidence < 0.9:
            return False

        if self._last_sent and (now - self._last_sent).total_seconds() < self.config.rate_limit_seconds:
            return False
        return True

    def get_notification_history(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        grouped = list(self.history)[-limit:]
        grouped.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        return grouped

    def _append_history(self, item: dict[str, Any]) -> None:
        self.history.append(item)

