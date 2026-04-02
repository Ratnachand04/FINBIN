from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from config.settings import get_settings
from database.models import Base, PriceData


async def seed() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    base_prices = {
        "BTCUSDT": 70000.0,
        "ETHUSDT": 3500.0,
        "DOGEUSDT": 0.25,
    }

    async with session_factory() as session:
        for symbol, base in base_prices.items():
            for idx in range(10):
                session.add(
                    PriceData(
                        ts=datetime.now(UTC),
                        symbol=symbol,
                        interval="15m",
                        open=base + idx,
                        high=(base + idx) * 1.002,
                        low=(base + idx) * 0.998,
                        close=(base + idx) * 1.001,
                        volume=1200 + idx,
                        quote_volume=(base + idx) * (1200 + idx),
                        meta={"seed": True},
                    )
                )
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
