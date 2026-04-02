from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import time
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Ensure repo root is importable when running this file directly.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.database import db_manager, execute_raw_sql, upsert
from backend.ml.model_trainer import ModelTrainer

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
BINANCE_MAX_LIMIT = 1000
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
DEFAULT_INTERVALS = ["15m", "1h", "4h", "1d"]
SUPPORTED_BLOCKCYPHER = {
    "BTC": {"chain": "btc", "symbol": "BTC", "unit_divisor": 100_000_000},
    "DOGE": {"chain": "doge", "symbol": "DOGE", "unit_divisor": 100_000_000},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect long-horizon Binance OHLCV and whale transactions for BTC/ETH/DOGE, "
            "then optionally train models with sentiment features."
        )
    )
    parser.add_argument("--years", type=float, default=8.0, help="Historical years to collect (default: 8)")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Binance symbols")
    parser.add_argument("--intervals", nargs="+", default=DEFAULT_INTERVALS, help="Binance intervals")
    parser.add_argument("--sleep-ms", type=int, default=120, help="Delay between Binance requests")
    parser.add_argument("--binance-timeout", type=float, default=30.0, help="Binance HTTP timeout seconds")
    parser.add_argument("--blockcypher-timeout", type=float, default=30.0, help="BlockCypher HTTP timeout seconds")
    parser.add_argument(
        "--blockcypher-max-blocks",
        type=int,
        default=20000,
        help="Maximum blocks scanned per supported chain",
    )
    parser.add_argument(
        "--blockcypher-max-tx",
        type=int,
        default=50000,
        help="Maximum tx details fetched per supported chain",
    )
    parser.add_argument(
        "--whale-threshold-usd",
        type=float,
        default=1_000_000,
        help="USD threshold to mark whale transactions",
    )
    parser.add_argument("--skip-binance", action="store_true", help="Skip Binance historical collection")
    parser.add_argument("--skip-whales", action="store_true", help="Skip whale transaction collection")
    parser.add_argument("--skip-sentiment-backfill", action="store_true", help="Skip sentiment aggregate backfill")
    parser.add_argument("--skip-train", action="store_true", help="Skip model training stage")
    return parser.parse_args()


def _chunked(rows: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [rows[idx : idx + chunk_size] for idx in range(0, len(rows), chunk_size)]


def _sql_quote(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return "NULL"
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


class LongHorizonCollector:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.start_ts = datetime.now(UTC) - timedelta(days=int(max(args.years, 0.1) * 365))
        self.end_ts = datetime.now(UTC)
        self.daily_close_cache: dict[str, dict[date, float]] = defaultdict(dict)

    async def run(self) -> None:
        logger.info("Collection range: %s -> %s", self.start_ts.isoformat(), self.end_ts.isoformat())

        if not self.args.skip_binance:
            await self.collect_binance_history()

        if not self.args.skip_sentiment_backfill:
            await self.backfill_sentiment_aggregates()

        if not self.args.skip_whales:
            await self.collect_whale_transactions()

        if not self.args.skip_train:
            await self.train_models()

    async def collect_binance_history(self) -> None:
        api_key = os.getenv("BINANCE_API_KEY", "").strip()
        headers = {"X-MBX-APIKEY": api_key} if api_key else {}
        timeout = self.args.binance_timeout

        symbols = [symbol.upper().strip() for symbol in self.args.symbols if symbol.strip()]
        intervals = [interval.strip() for interval in self.args.intervals if interval.strip()]
        if not symbols or not intervals:
            logger.warning("No symbols/intervals passed for Binance collection")
            return

        logger.info("Starting Binance historical collection for %s", ", ".join(symbols))
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            for symbol in symbols:
                for interval in intervals:
                    rows = await self._fetch_klines(client=client, symbol=symbol, interval=interval)
                    if not rows:
                        logger.warning("No Binance rows returned for %s %s", symbol, interval)
                        continue
                    await self._upsert_price_rows(rows)
                    logger.info("Stored %s rows for %s %s", len(rows), symbol, interval)

    async def _fetch_klines(self, client: httpx.AsyncClient, symbol: str, interval: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = int(self.start_ts.timestamp() * 1000)
        end_ms = int(self.end_ts.timestamp() * 1000)

        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": BINANCE_MAX_LIMIT,
            }
            response = await client.get(f"{BINANCE_BASE_URL}/api/v3/klines", params=params)
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break

            for item in batch:
                open_time_ms = int(item[0])
                ts = datetime.fromtimestamp(open_time_ms / 1000, tz=UTC)
                close_price = float(item[4])
                self.daily_close_cache[symbol][ts.date()] = close_price
                rows.append(
                    {
                        "ts": ts,
                        "symbol": symbol,
                        "interval": interval,
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": close_price,
                        "volume": float(item[5]),
                        "quote_volume": float(item[7]),
                        "trade_count": int(item[8]),
                        "source": "binance_rest_klines",
                        "metadata": {
                            "close_time": int(item[6]),
                            "taker_buy_base": float(item[9]),
                            "taker_buy_quote": float(item[10]),
                        },
                    }
                )

            last_open_time = int(batch[-1][0])
            next_cursor = last_open_time + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor

            if len(batch) < BINANCE_MAX_LIMIT:
                break
            await asyncio.sleep(max(0, self.args.sleep_ms) / 1000)

        return rows

    async def _upsert_price_rows(self, rows: list[dict[str, Any]]) -> None:
        async with db_manager.session_factory() as session:
            col_rows = (
                await execute_raw_sql(
                    session,
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'price_data'",
                )
            ).all()
            allowed_columns = {str(item.column_name) for item in col_rows}

            for chunk in _chunked(rows, 1000):
                for row in chunk:
                    values = {key: value for key, value in row.items() if key in allowed_columns}
                    update_columns = [
                        col
                        for col in [
                            "open",
                            "high",
                            "low",
                            "close",
                            "volume",
                            "quote_volume",
                            "trade_count",
                            "source",
                            "metadata",
                        ]
                        if col in values
                    ]
                    await upsert(
                        session=session,
                        table_name="price_data",
                        values=values,
                        conflict_columns=["symbol", "interval", "ts"],
                        update_columns=update_columns,
                    )
            await session.commit()

    async def backfill_sentiment_aggregates(self) -> None:
        symbols = [self._coin_from_symbol(symbol) for symbol in self.args.symbols]
        symbols = [coin for coin in symbols if coin]
        if not symbols:
            return

        logger.info("Backfilling 1h sentiment aggregates for %s", ", ".join(symbols))
        async with db_manager.session_factory() as session:
            for coin in symbols:
                await execute_raw_sql(
                    session,
                    "INSERT INTO sentiment_aggregates "
                    "(ts, window, symbol, sample_count, avg_sentiment, sentiment_stddev, bullish_ratio, "
                    "bearish_ratio, neutral_ratio, weighted_score, metadata, created_at) "
                    "SELECT DATE_TRUNC('hour', ts) AS bucket_ts, '1h', symbol, "
                    "COUNT(*)::int AS sample_count, "
                    "COALESCE(AVG(sentiment_score), 0) AS avg_sentiment, "
                    "STDDEV_POP(sentiment_score) AS sentiment_stddev, "
                    "AVG(CASE WHEN sentiment_label = 'BULLISH' THEN 1 ELSE 0 END) AS bullish_ratio, "
                    "AVG(CASE WHEN sentiment_label = 'BEARISH' THEN 1 ELSE 0 END) AS bearish_ratio, "
                    "AVG(CASE WHEN sentiment_label = 'NEUTRAL' THEN 1 ELSE 0 END) AS neutral_ratio, "
                    "COALESCE(AVG(sentiment_score * COALESCE(confidence, 0.5)), 0) AS weighted_score, "
                    "CAST(:metadata AS jsonb), NOW() "
                    "FROM sentiment_scores "
                    "WHERE symbol = :coin AND ts BETWEEN :start_ts AND :end_ts "
                    "GROUP BY DATE_TRUNC('hour', ts), symbol "
                    "ON CONFLICT (window, symbol, ts) DO UPDATE SET "
                    "sample_count = EXCLUDED.sample_count, "
                    "avg_sentiment = EXCLUDED.avg_sentiment, "
                    "sentiment_stddev = EXCLUDED.sentiment_stddev, "
                    "bullish_ratio = EXCLUDED.bullish_ratio, "
                    "bearish_ratio = EXCLUDED.bearish_ratio, "
                    "neutral_ratio = EXCLUDED.neutral_ratio, "
                    "weighted_score = EXCLUDED.weighted_score, "
                    "metadata = EXCLUDED.metadata",
                    {
                        "coin": coin,
                        "start_ts": self.start_ts,
                        "end_ts": self.end_ts,
                        "metadata": json.dumps({"backfill": "collect_long_horizon_training_data"}),
                    },
                )
            await session.commit()

    async def collect_whale_transactions(self) -> None:
        token = os.getenv("BLOCKCYPHER_API_KEY", "").strip()
        if not token:
            logger.warning("BLOCKCYPHER_API_KEY is empty; skipping BlockCypher whale collection")
            return

        await self._ensure_whale_tables()

        timeout = self.args.blockcypher_timeout
        max_blocks = max(0, self.args.blockcypher_max_blocks)
        max_txs = max(0, self.args.blockcypher_max_tx)
        requested_coins = {self._coin_from_symbol(symbol) for symbol in self.args.symbols}
        blockcypher_targets = [
            coin for coin in SUPPORTED_BLOCKCYPHER.keys() if coin in requested_coins
        ]

        if not blockcypher_targets:
            logger.warning(
                "No requested symbols are supported by BlockCypher in this collector; skipping whales"
            )
            return

        async with httpx.AsyncClient(timeout=timeout) as client:
            for coin in blockcypher_targets:
                cfg = SUPPORTED_BLOCKCYPHER[coin]
                chain = cfg["chain"]
                logger.info("Collecting BlockCypher whale transactions for %s", coin)
                fetched_blocks, fetched_txs, inserted_rows = await self._collect_chain_whales(
                    client=client,
                    token=token,
                    coin=coin,
                    chain=chain,
                    unit_divisor=int(cfg["unit_divisor"]),
                    max_blocks=max_blocks,
                    max_txs=max_txs,
                )
                logger.info(
                    "BlockCypher %s complete: blocks=%s tx_details=%s inserted=%s",
                    coin,
                    fetched_blocks,
                    fetched_txs,
                    inserted_rows,
                )

        # BlockCypher does not support Ethereum chain endpoints.
        if "ETH" in requested_coins:
            logger.warning(
                "BlockCypher does not provide Ethereum chain history via this endpoint. "
                "ETH whale on-chain backfill is skipped in this script."
            )

    async def _ensure_whale_tables(self) -> None:
        async with db_manager.session_factory() as session:
            await execute_raw_sql(
                session,
                """
                CREATE TABLE IF NOT EXISTS onchain_transactions (
                    id BIGSERIAL PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL,
                    chain TEXT NOT NULL,
                    tx_hash TEXT NOT NULL,
                    block_number BIGINT,
                    symbol TEXT NOT NULL,
                    from_address TEXT NOT NULL,
                    to_address TEXT NOT NULL,
                    amount NUMERIC(38, 18) NOT NULL,
                    amount_usd NUMERIC(38, 8),
                    is_whale BOOLEAN NOT NULL DEFAULT FALSE,
                    whale_threshold_usd NUMERIC(38, 8),
                    flow_direction TEXT,
                    exchange_name TEXT,
                    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (chain, tx_hash)
                )
                """,
            )
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
            await session.commit()

    async def _collect_chain_whales(
        self,
        client: httpx.AsyncClient,
        token: str,
        coin: str,
        chain: str,
        unit_divisor: int,
        max_blocks: int,
        max_txs: int,
    ) -> tuple[int, int, int]:
        base = f"https://api.blockcypher.com/v1/{chain}/main"
        chain_resp = await client.get(base, params={"token": token})
        chain_resp.raise_for_status()
        chain_data = chain_resp.json()
        height = int(chain_data.get("height", 0))

        blocks_scanned = 0
        tx_details_fetched = 0
        inserted = 0
        min_block_time = self.start_ts

        while height > 0 and blocks_scanned < max_blocks and tx_details_fetched < max_txs:
            block_resp = await client.get(f"{base}/blocks/{height}", params={"txstart": 1, "limit": 500, "token": token})
            if block_resp.status_code == 429:
                await asyncio.sleep(1.5)
                continue
            block_resp.raise_for_status()
            block = block_resp.json()

            block_time = self._parse_ts(block.get("time"))
            if block_time and block_time < min_block_time:
                break

            txids = block.get("txids") or []
            if not isinstance(txids, list):
                txids = []

            for tx_hash in txids:
                if tx_details_fetched >= max_txs:
                    break
                tx_resp = await client.get(f"{base}/txs/{tx_hash}", params={"token": token})
                if tx_resp.status_code == 429:
                    await asyncio.sleep(1.2)
                    continue
                if tx_resp.status_code >= 400:
                    continue
                tx = tx_resp.json()
                tx_details_fetched += 1

                tx_time = self._parse_ts(tx.get("confirmed") or tx.get("received"))
                if tx_time is None or tx_time < self.start_ts:
                    continue

                total_base = float(tx.get("total") or 0.0)
                amount_coin = total_base / float(unit_divisor)
                if amount_coin <= 0:
                    continue

                symbol = coin.upper()
                amount_usd = amount_coin * self._estimate_usd(symbol, tx_time)
                is_whale = amount_usd >= self.args.whale_threshold_usd

                from_address = self._extract_first_input_address(tx)
                to_address = self._extract_first_output_address(tx)

                onchain_row = {
                    "ts": tx_time,
                    "chain": symbol,
                    "tx_hash": str(tx_hash),
                    "block_number": int(tx.get("block_height") or 0),
                    "symbol": symbol,
                    "from_address": from_address,
                    "to_address": to_address,
                    "amount": amount_coin,
                    "amount_usd": amount_usd,
                    "is_whale": is_whale,
                    "whale_threshold_usd": float(self.args.whale_threshold_usd),
                    "flow_direction": "wallet_to_wallet",
                    "exchange_name": None,
                    "tags": ["blockcypher"],
                    "metadata": {
                        "source": "blockcypher",
                        "fees_base": tx.get("fees"),
                        "confirmations": tx.get("confirmations"),
                        "inputs_count": len(tx.get("inputs") or []),
                        "outputs_count": len(tx.get("outputs") or []),
                    },
                }

                async with db_manager.session_factory() as session:
                    await upsert(
                        session=session,
                        table_name="onchain_transactions",
                        values=onchain_row,
                        conflict_columns=["chain", "tx_hash"],
                        update_columns=[
                            "amount",
                            "amount_usd",
                            "is_whale",
                            "whale_threshold_usd",
                            "flow_direction",
                            "exchange_name",
                            "tags",
                            "metadata",
                        ],
                    )

                    if is_whale:
                        whale_row = {
                            "ts": tx_time,
                            "chain": symbol,
                            "tx_hash": str(tx_hash),
                            "symbol": symbol,
                            "amount_usd": amount_usd,
                            "flow_direction": "wallet_to_wallet",
                            "is_whale": True,
                            "metadata": {
                                "source": "blockcypher",
                                "amount_coin": amount_coin,
                                "block_height": int(tx.get("block_height") or 0),
                            },
                        }
                        await upsert(
                            session=session,
                            table_name="whale_transactions",
                            values=whale_row,
                            conflict_columns=["tx_hash"],
                            update_columns=["amount_usd", "flow_direction", "is_whale", "metadata"],
                        )

                    await session.commit()
                inserted += 1

            blocks_scanned += 1
            height -= 1

            if blocks_scanned % 100 == 0:
                logger.info(
                    "BlockCypher %s progress: blocks=%s tx_details=%s inserted=%s",
                    coin,
                    blocks_scanned,
                    tx_details_fetched,
                    inserted,
                )

        return blocks_scanned, tx_details_fetched, inserted

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    @staticmethod
    def _extract_first_input_address(tx: dict[str, Any]) -> str:
        inputs = tx.get("inputs") or []
        if not inputs:
            return "unknown"
        addresses = inputs[0].get("addresses") or []
        return str(addresses[0]) if addresses else "unknown"

    @staticmethod
    def _extract_first_output_address(tx: dict[str, Any]) -> str:
        outputs = tx.get("outputs") or []
        if not outputs:
            return "unknown"
        addresses = outputs[0].get("addresses") or []
        return str(addresses[0]) if addresses else "unknown"

    @staticmethod
    def _coin_from_symbol(symbol: str) -> str:
        symbol = symbol.upper().strip()
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol

    def _estimate_usd(self, coin: str, tx_time: datetime) -> float:
        symbol = f"{coin.upper()}USDT"
        day_map = self.daily_close_cache.get(symbol, {})
        price = day_map.get(tx_time.date())
        if price is not None:
            return float(price)

        if not day_map:
            return 0.0

        nearest_day = min(day_map.keys(), key=lambda d: abs((d - tx_time.date()).days))
        return float(day_map.get(nearest_day, 0.0))

    async def train_models(self) -> None:
        lookback_days = int(max(self.args.years, 0.1) * 365)
        trainer = ModelTrainer()
        coins = [self._coin_from_symbol(symbol) for symbol in self.args.symbols]

        logger.info("Starting model training with lookback_days=%s", lookback_days)
        for coin in coins:
            start = time.perf_counter()
            try:
                result = await trainer.train_pipeline(coin=coin, lookback_days=lookback_days)
                elapsed = time.perf_counter() - start
                logger.info(
                    "Training complete for %s in %.2fs: status=%s",
                    coin,
                    elapsed,
                    result.get("status", "unknown"),
                )
            except Exception as exc:
                logger.exception("Training failed for %s: %s", coin, exc)


async def async_main() -> None:
    load_dotenv()
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    collector = LongHorizonCollector(args)
    try:
        await collector.run()
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(async_main())
