from __future__ import annotations

import csv
import asyncio
import importlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Iterable, Mapping

from prometheus_client import Gauge, Histogram
from sqlalchemy import JSON, Boolean, Column, DateTime, Float, MetaData, String, Table, Text, func, insert, text
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert
from sqlalchemy.engine import Result
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_scoped_session, async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.orm_base import Base

logger = logging.getLogger(__name__)

DB_ACTIVE_CONNECTIONS = Gauge("binfin_db_active_connections", "Current active DB connections")
DB_QUERY_SECONDS = Histogram(
    "binfin_db_query_seconds",
    "Database query latency in seconds",
    buckets=(0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)
DB_UP = Gauge("binfin_db_up", "Database health status (1=up, 0=down)")
REDIS_UP = Gauge("binfin_redis_up", "Redis health status (1=up, 0=down)")


def _load_redis_module() -> Any:
    try:
        return importlib.import_module("redis.asyncio")
    except Exception as exc:  # pragma: no cover - dependency bootstrap issue
        raise RuntimeError("redis package is required for Redis connection manager") from exc


def _build_database_url() -> str:
    env_database_url = os.getenv("DATABASE_URL")
    if env_database_url:
        if env_database_url.startswith("postgresql://"):
            return env_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return env_database_url
    return (
        "postgresql+asyncpg://"
        f"{settings.postgres_user}:{settings.postgres_password}@"
        f"{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


def _build_redis_url() -> str:
    env_redis_url = os.getenv("REDIS_URL")
    if env_redis_url:
        return env_redis_url
    return f"redis://{settings.redis_host}:{settings.redis_port}/0"


metadata = MetaData()


class DatabaseManager:
    def __init__(self) -> None:
        self._redis_mod = _load_redis_module()
        self.database_url = _build_database_url()
        self.redis_url = _build_redis_url()
        self.engine = self._create_engine()
        self._session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )
        self.session_factory: async_scoped_session[AsyncSession] = async_scoped_session(
            self._session_factory,
            scopefunc=asyncio.current_task,
        )
        self.redis_pool = self._create_redis_pool()
        self.redis_client = self._redis_mod.Redis(connection_pool=self.redis_pool)
        self._health_task: asyncio.Task[Any] | None = None
        self._health_interval_seconds = 30

    def _create_engine(self) -> AsyncEngine:
        statement_timeout_ms = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
        return create_async_engine(
            self.database_url,
            pool_size=20,
            max_overflow=0,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"server_settings": {"statement_timeout": str(statement_timeout_ms)}},
        )

    def _create_redis_pool(self) -> Any:
        return self._redis_mod.ConnectionPool.from_url(
            self.redis_url,
            max_connections=100,
            decode_responses=True,
            health_check_interval=30,
        )

    async def initialize(self) -> None:
        await self.init_database()
        await self.start_health_monitoring()

    async def init_database(self) -> None:
        await self._create_tables_if_not_exists()
        await self.run_migrations()
        await self.seed_initial_data()

    async def _create_tables_if_not_exists(self) -> None:
        logger.info("Ensuring ORM tables exist")
        # Import model modules so all tables are registered in Base.metadata before create_all.
        from backend import models as _models  # noqa: F401

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def run_migrations(self) -> None:
        logger.info("Running migrations (if alembic is configured)")
        if not os.path.exists("alembic.ini"):
            logger.warning("alembic.ini not found. Skipping migrations.")
            return
        try:
            from alembic import command
            from alembic.config import Config

            cfg = Config("alembic.ini")
            await asyncio.to_thread(command.upgrade, cfg, "head")
        except Exception as exc:
            logger.exception("Migration step failed: %s", exc)
            raise

    async def seed_initial_data(self) -> None:
        logger.info("Seeding default coin configs")
        tracked = os.getenv("TRACKED_COINS", "BTC,ETH,DOGE")
        defaults = [coin.strip().upper() for coin in tracked.split(",") if coin.strip()]
        async with self.session_factory() as session:
            try:
                for symbol in defaults:
                    await upsert(
                        session=session,
                        table_name="coin_configs",
                        values={
                            "symbol": symbol,
                            "is_enabled": True,
                            "min_signal_confidence": float(os.getenv("MIN_SIGNAL_CONFIDENCE", "0.75")),
                            "min_signal_strength": float(os.getenv("MIN_SIGNAL_STRENGTH", "7.0")),
                        },
                        conflict_columns=["symbol"],
                        update_columns=["is_enabled", "min_signal_confidence", "min_signal_strength"],
                    )
                await session.commit()
            except Exception as exc:
                await session.rollback()
                logger.exception("Failed to seed initial coin configs: %s", exc)
                raise

    async def ensure_whale_transactions_loaded(self) -> None:
        whale_files = self._whale_export_files()
        if not whale_files:
            logger.info("No whale export files found; skipping whale dataset bootstrap")
            return

        async with self.session_factory() as session:
            await execute_raw_sql(
                session,
                """
                CREATE TABLE IF NOT EXISTS whale_transactions (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL,
                    chain TEXT NOT NULL,
                    tx_hash TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    amount_usd DOUBLE PRECISION NOT NULL,
                    flow_direction TEXT,
                    is_whale BOOLEAN NOT NULL DEFAULT TRUE,
                    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
                )
                """,
            )
            result = await execute_raw_sql(session, "SELECT COUNT(*) AS count FROM whale_transactions")
            count_row = result.first()
            current_count = int(count_row.count or 0) if count_row else 0
            if current_count > 0:
                logger.info("Whale transactions already present (%s rows); bootstrap skipped", current_count)
                return

            rows = self._parse_whale_export_rows(whale_files)
            if not rows:
                logger.warning("No whale rows parsed from export files")
                return

            logger.info("Bootstrapping whale_transactions with %s rows from %s files", len(rows), len(whale_files))
            for row in rows:
                await upsert(
                    session=session,
                    table_name="whale_transactions",
                    values=row,
                    conflict_columns=["tx_hash"],
                    update_columns=["amount_usd", "flow_direction", "is_whale", "metadata"],
                )
            await session.commit()

    def _parse_whale_export_rows(self, files: list[Path]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in files:
            with path.open("r", encoding="utf-8") as file_handle:
                for raw_line in file_handle:
                    parsed = self._parse_sql_tuple(raw_line)
                    if not parsed or len(parsed) < 8:
                        continue
                    try:
                        ts = self._parse_timestamp(parsed[0])
                        metadata = json.loads(parsed[7]) if parsed[7] not in {"NULL", ""} else {}
                        rows.append(
                            {
                                "ts": ts,
                                "chain": str(parsed[1]).upper(),
                                "tx_hash": str(parsed[2]),
                                "symbol": str(parsed[3]).upper(),
                                "amount_usd": float(parsed[4]),
                                "flow_direction": None if parsed[5] in {"NULL", ""} else str(parsed[5]),
                                "is_whale": str(parsed[6]).upper() in {"TRUE", "T", "1"},
                                "metadata": metadata,
                            }
                        )
                    except Exception as exc:
                        logger.warning("Skipping malformed whale row in %s: %s", path, exc)
                        continue
        return rows

    def _parse_sql_tuple(self, line: str) -> list[str] | None:
        text = line.strip().rstrip(",")
        if not text.startswith("(") or not text.endswith(")"):
            return None
        inner = text[1:-1]
        try:
            return next(csv.reader([inner], delimiter=",", quotechar="'", skipinitialspace=True))
        except Exception:
            return None

    def _parse_timestamp(self, value: str) -> datetime:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

    async def check_db_health(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            DB_UP.set(1)
            pool = self.engine.sync_engine.pool
            checked_out = getattr(pool, "checkedout", None)
            if callable(checked_out):
                DB_ACTIVE_CONNECTIONS.set(float(checked_out()))
            return True
        except Exception as exc:
            DB_UP.set(0)
            logger.exception("Database health check failed: %s", exc)
            return False

    async def check_redis_health(self) -> bool:
        try:
            healthy = bool(await self.redis_client.ping())
            REDIS_UP.set(1 if healthy else 0)
            return healthy
        except Exception as exc:
            REDIS_UP.set(0)
            logger.exception("Redis health check failed: %s", exc)
            return False

    async def recreate_engine(self) -> None:
        logger.warning("Recreating async DB engine")
        await self.engine.dispose()
        self.engine = self._create_engine()
        self._session_factory.configure(bind=self.engine)

    async def recreate_redis(self) -> None:
        logger.warning("Recreating Redis connection pool")
        await self.redis_client.aclose()
        await self.redis_pool.disconnect()
        self.redis_pool = self._create_redis_pool()
        self.redis_client = self._redis_mod.Redis(connection_pool=self.redis_pool)

    async def _health_monitor_loop(self) -> None:
        while True:
            if not await self.check_db_health():
                await self.recreate_engine()
            if not await self.check_redis_health():
                await self.recreate_redis()
            await asyncio.sleep(self._health_interval_seconds)

    async def start_health_monitoring(self) -> None:
        if self._health_task and not self._health_task.done():
            return
        self._health_task = asyncio.create_task(self._health_monitor_loop())
        logger.info("Started DB/Redis health monitoring")

    async def stop_health_monitoring(self) -> None:
        if not self._health_task:
            return
        self._health_task.cancel()
        try:
            await self._health_task
        except asyncio.CancelledError:
            pass
        self._health_task = None
        logger.info("Stopped DB/Redis health monitoring")

    async def close(self) -> None:
        await self.stop_health_monitoring()
        await self.redis_client.aclose()
        await self.redis_pool.disconnect()
        await self.engine.dispose()
        logger.info("Database and Redis resources closed")


db_manager = DatabaseManager()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    session: AsyncSession = db_manager.session_factory()
    try:
        yield session
    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("Database session failed: %s", exc)
        raise
    finally:
        await session.close()


async def get_redis() -> AsyncGenerator[Any, None]:
    try:
        yield db_manager.redis_client
    except Exception as exc:
        logger.exception("Redis operation failed: %s", exc)
        raise


@asynccontextmanager
async def db_session_context() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db():
        yield session


@asynccontextmanager
async def redis_context() -> AsyncGenerator[Any, None]:
    async for client in get_redis():
        yield client


async def execute_raw_sql(session: AsyncSession, sql: str, params: Mapping[str, Any] | None = None) -> Result[Any]:
    start = time.perf_counter()
    try:
        return await session.execute(text(sql), params or {})
    except Exception as exc:
        logger.exception("Raw SQL execution failed: %s", exc)
        raise
    finally:
        DB_QUERY_SECONDS.observe(time.perf_counter() - start)


async def _reflect_table(session: AsyncSession, table_name: str) -> Table:
    reflect_md = MetaData()

    def _sync_reflect(sync_session: Any) -> Table:
        reflect_md.reflect(bind=sync_session.connection(), only=[table_name])
        return reflect_md.tables[table_name]

    return await session.run_sync(_sync_reflect)


async def bulk_insert(session: AsyncSession, table_name: str, rows: Iterable[Mapping[str, Any]]) -> None:
    payload = list(rows)
    if not payload:
        return
    table = await _reflect_table(session, table_name)
    start = time.perf_counter()
    try:
        await session.execute(insert(table), payload)
    except Exception as exc:
        logger.exception("Bulk insert failed for %s: %s", table_name, exc)
        raise
    finally:
        DB_QUERY_SECONDS.observe(time.perf_counter() - start)


async def upsert(
    session: AsyncSession,
    table_name: str,
    values: Mapping[str, Any],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
) -> None:
    table = await _reflect_table(session, table_name)
    stmt = pg_insert(table).values(**values)

    if update_columns:
        set_values = {column: values[column] for column in update_columns if column in values}
    else:
        set_values = {
            column.name: stmt.excluded[column.name]
            for column in table.columns
            if column.name not in conflict_columns
        }

    start = time.perf_counter()
    try:
        await session.execute(stmt.on_conflict_do_update(index_elements=conflict_columns, set_=set_values))
    except Exception as exc:
        logger.exception("Upsert failed for %s: %s", table_name, exc)
        raise
    finally:
        DB_QUERY_SECONDS.observe(time.perf_counter() - start)