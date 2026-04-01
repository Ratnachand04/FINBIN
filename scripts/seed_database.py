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

    async with session_factory() as session:
        for idx in range(10):
            session.add(
                PriceData(
                    ts=datetime.now(UTC),
                    symbol="BTCUSDT",
                    interval="15m",
                    open=70000 + idx,
                    high=70020 + idx,
                    low=69980 + idx,
                    close=70010 + idx,
                    volume=1200 + idx,
                    quote_volume=84000000 + idx,
                    meta={"seed": True},
                )
            )
        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
