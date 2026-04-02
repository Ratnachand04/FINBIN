from __future__ import annotations

import asyncio
import logging

from signals.notifier import SignalNotifier
from signals.signal_generator import MarketSnapshot, SignalGenerator

logger = logging.getLogger(__name__)


async def run_signal_loop() -> None:
    generator = SignalGenerator()
    notifier = SignalNotifier()

    while True:
        samples = [
            MarketSnapshot("BTCUSDT", 70000, 70500, 0.62, 0.25, 0.82),
            MarketSnapshot("ETHUSDT", 3500, 3560, 0.58, 0.22, 0.79),
            MarketSnapshot("DOGEUSDT", 0.25, 0.27, 0.55, 0.30, 0.76),
        ]
        for sample in samples:
            signal = generator.generate(sample)
            if signal:
                notifier.send(signal)
                logger.info("signal generated: %s %s", signal.symbol, signal.side)
        await asyncio.sleep(30)


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    asyncio.run(run_signal_loop())
