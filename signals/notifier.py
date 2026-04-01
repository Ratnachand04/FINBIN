from __future__ import annotations

import logging
from dataclasses import asdict

from signals.signal_generator import TradeSignal

logger = logging.getLogger(__name__)


class SignalNotifier:
    def send(self, signal: TradeSignal) -> None:
        payload = asdict(signal)
        logger.info("signal_notification %s", payload)
