from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from prometheus_client import Counter, Gauge, Histogram

from backend.database import bulk_insert, db_manager, upsert

logger = logging.getLogger(__name__)

PRICE_TICKS_TOTAL = Counter("binfin_price_ticks_total", "Total realtime price ticks processed")
PRICE_WS_UP = Gauge("binfin_price_ws_up", "Binance websocket health status (1=up, 0=down)")
PRICE_WRITE_LATENCY = Histogram(
    "binfin_price_write_seconds",
    "Price/indicator persistence latency",
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
)


class PriceCollector:
    DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
    VALID_INTERVALS = {"15m", "1h", "4h", "1d"}
    INTERVAL_TO_MINUTES = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}

    def __init__(self) -> None:
        self.binance_rest_url = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
        self.cache_flush_seconds = 1
        self.db_flush_seconds = 15 * 60
        self.historical_refresh_seconds = 60 * 30
        self._shutdown = asyncio.Event()
        self._price_buffer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._last_cache_flush = 0.0
        self._last_db_flush = 0.0
        self._python_binance = self._load_optional("binance")
        self._pandas = self._load_optional("pandas")
        self._pandas_ta = self._load_optional("pandas_ta")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def __aenter__(self) -> "PriceCollector":
        PRICE_WS_UP.set(1)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.shutdown()

    async def stream_realtime_prices(self, symbols: list[str] | None = None) -> None:
        symbols = symbols or self.DEFAULT_SYMBOLS
        stream_symbols = [symbol.lower() for symbol in symbols]

        if not self._python_binance:
            await self._stream_realtime_prices_http_fallback(symbols)
            return

        AsyncClient = getattr(self._python_binance, "AsyncClient", None)
        BinanceSocketManager = getattr(self._python_binance, "BinanceSocketManager", None)
        if not AsyncClient or not BinanceSocketManager:
            await self._stream_realtime_prices_http_fallback(symbols)
            return

        while not self._shutdown.is_set():
            client = None
            try:
                client = await AsyncClient.create()
                bsm = BinanceSocketManager(client)
                stream_name = "/".join([f"{sym}@ticker" for sym in stream_symbols])
                socket = bsm.multiplex_socket(stream_name.split("/"))

                async with socket as stream:
                    PRICE_WS_UP.set(1)
                    while not self._shutdown.is_set():
                        msg = await asyncio.wait_for(stream.recv(), timeout=30)
                        tick = self._parse_ws_tick(msg)
                        if not tick:
                            continue

                        self._price_buffer[tick["symbol"]].append(tick)
                        PRICE_TICKS_TOTAL.inc()

                        now = time.time()
                        if now - self._last_cache_flush >= self.cache_flush_seconds:
                            await self._flush_cache()
                            self._last_cache_flush = now
                        if now - self._last_db_flush >= self.db_flush_seconds:
                            await self._flush_db()
                            self._last_db_flush = now
            except Exception as exc:
                PRICE_WS_UP.set(0)
                logger.exception("WebSocket stream error, reconnecting: %s", exc)
                await asyncio.sleep(3)
            finally:
                try:
                    if client is not None:
                        await client.close_connection()
                except Exception:
                    pass

    async def _stream_realtime_prices_http_fallback(self, symbols: list[str]) -> None:
        logger.warning("python-binance unavailable; using HTTP polling fallback")
        async with httpx.AsyncClient(timeout=10) as client:
            while not self._shutdown.is_set():
                try:
                    for symbol in symbols:
                        endpoint = f"{self.binance_rest_url}/api/v3/ticker/24hr"
                        response = await client.get(endpoint, params={"symbol": symbol})
                        response.raise_for_status()
                        payload = response.json()
                        tick = {
                            "symbol": payload["symbol"],
                            "price": float(payload.get("lastPrice", 0.0)),
                            "volume": float(payload.get("volume", 0.0)),
                            "quote_volume": float(payload.get("quoteVolume", 0.0)),
                            "trade_count": int(payload.get("count", 0)),
                            "ts": datetime.now(UTC),
                        }
                        self._price_buffer[symbol].append(tick)
                        PRICE_TICKS_TOTAL.inc()

                    await self._flush_cache()
                    if time.time() - self._last_db_flush >= self.db_flush_seconds:
                        await self._flush_db()
                        self._last_db_flush = time.time()
                except Exception as exc:
                    logger.exception("HTTP fallback polling failed: %s", exc)
                await asyncio.sleep(1)

    async def fetch_historical_ohlcv(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 1000,
    ) -> Any:
        if interval not in self.VALID_INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")

        if self._pandas is None:
            raise RuntimeError("pandas is required for historical OHLCV DataFrame output")

        # Ensure at least 6 months. Binance max limit per call is 1000, so we iterate.
        start_time = datetime.now(UTC) - timedelta(days=180)
        klines: list[list[Any]] = []
        current_start_ms = int(start_time.timestamp() * 1000)

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                endpoint = f"{self.binance_rest_url}/api/v3/klines"
                params = {
                    "symbol": symbol.upper(),
                    "interval": interval,
                    "limit": min(1000, limit),
                    "startTime": current_start_ms,
                }
                response = await client.get(endpoint, params=params)
                response.raise_for_status()
                batch = response.json()
                if not batch:
                    break

                klines.extend(batch)
                last_open_time = int(batch[-1][0])
                next_start_ms = last_open_time + 1
                if next_start_ms <= current_start_ms:
                    break
                current_start_ms = next_start_ms
                if len(batch) < min(1000, limit):
                    break
                if datetime.fromtimestamp(last_open_time / 1000, tz=UTC) >= datetime.now(UTC):
                    break

        df = self._pandas.DataFrame(
            klines,
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trade_count",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )
        if df.empty:
            return df

        for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
            df[col] = df[col].astype(float)
        df["trade_count"] = df["trade_count"].astype(int)
        df["ts"] = self._pandas.to_datetime(df["open_time"], unit="ms", utc=True)
        df["symbol"] = symbol.upper()
        df["interval"] = interval
        return df

    async def calculate_technical_indicators(self, df: Any) -> Any:
        if self._pandas is None or df is None or df.empty:
            return df

        if self._pandas_ta:
            ta = self._pandas_ta
            df["sma_7"] = ta.sma(df["close"], length=7)
            df["sma_25"] = ta.sma(df["close"], length=25)
            df["sma_99"] = ta.sma(df["close"], length=99)
            df["ema_12"] = ta.ema(df["close"], length=12)
            df["ema_26"] = ta.ema(df["close"], length=26)
            df["ema_50"] = ta.ema(df["close"], length=50)
            df["ema_200"] = ta.ema(df["close"], length=200)
            df["rsi_14"] = ta.rsi(df["close"], length=14)
            df["rsi_21"] = ta.rsi(df["close"], length=21)
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None and not macd.empty:
                df["macd"] = macd.iloc[:, 0]
                df["macd_signal"] = macd.iloc[:, 1]
                df["macd_hist"] = macd.iloc[:, 2]
            stoch = ta.stoch(df["high"], df["low"], df["close"])
            if stoch is not None and not stoch.empty:
                df["stoch_k"] = stoch.iloc[:, 0]
                df["stoch_d"] = stoch.iloc[:, 1]
            df["atr_14"] = ta.atr(df["high"], df["low"], df["close"], length=14)
            bbands = ta.bbands(df["close"], length=20, std=2)
            if bbands is not None and not bbands.empty:
                df["bb_lower"] = bbands.iloc[:, 0]
                df["bb_mid"] = bbands.iloc[:, 1]
                df["bb_upper"] = bbands.iloc[:, 2]
            df["obv"] = ta.obv(df["close"], df["volume"])
            df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])
            df["adx_14"] = ta.adx(df["high"], df["low"], df["close"], length=14).iloc[:, 0]
        else:
            # Lightweight fallback indicators when pandas-ta/ta-lib are unavailable.
            df["sma_7"] = df["close"].rolling(window=7).mean()
            df["sma_25"] = df["close"].rolling(window=25).mean()
            df["sma_99"] = df["close"].rolling(window=99).mean()
            df["ema_12"] = df["close"].ewm(span=12, adjust=False).mean()
            df["ema_26"] = df["close"].ewm(span=26, adjust=False).mean()
            df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
            df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs = gain / loss.replace(0, None)
            df["rsi_14"] = 100 - (100 / (1 + rs))
            df["rsi_21"] = 100 - (100 / (1 + (delta.where(delta > 0, 0.0).rolling(21).mean() /
                                               (-delta.where(delta < 0, 0.0)).rolling(21).mean().replace(0, None))))
            df["macd"] = df["ema_12"] - df["ema_26"]
            df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
            df["macd_hist"] = df["macd"] - df["macd_signal"]
            rolling_std = df["close"].rolling(20).std()
            bb_mid = df["close"].rolling(20).mean()
            df["bb_mid"] = bb_mid
            df["bb_upper"] = bb_mid + (rolling_std * 2)
            df["bb_lower"] = bb_mid - (rolling_std * 2)
            df["atr_14"] = (df["high"] - df["low"]).rolling(14).mean()
            df["obv"] = (df["volume"] * ((df["close"].diff() > 0).astype(int) * 2 - 1)).cumsum()
            typical = (df["high"] + df["low"] + df["close"]) / 3
            df["vwap"] = (typical * df["volume"]).cumsum() / df["volume"].replace(0, None).cumsum()
            df["adx_14"] = (df["high"] - df["low"]).rolling(14).mean()
            df["stoch_k"] = ((df["close"] - df["low"].rolling(14).min()) /
                             (df["high"].rolling(14).max() - df["low"].rolling(14).min())) * 100
            df["stoch_d"] = df["stoch_k"].rolling(3).mean()

        return df

    async def save_price_data(self, data: list[dict[str, Any]]) -> None:
        if not data:
            return
        start = time.perf_counter()
        async with db_manager.session_factory() as session:
            try:
                for row in data:
                    await upsert(
                        session=session,
                        table_name="price_data",
                        values=row,
                        conflict_columns=["symbol", "interval", "ts"],
                        update_columns=["open", "high", "low", "close", "volume", "quote_volume", "trade_count"],
                    )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                PRICE_WRITE_LATENCY.observe(time.perf_counter() - start)

    async def save_technical_indicators(self, data: list[dict[str, Any]]) -> None:
        if not data:
            return
        start = time.perf_counter()
        async with db_manager.session_factory() as session:
            try:
                await bulk_insert(session, "technical_indicators", data)
                await session.commit()
            except Exception:
                await session.rollback()
                # fallback to upsert to handle duplicates in batch processing
                for row in data:
                    await upsert(
                        session=session,
                        table_name="technical_indicators",
                        values=row,
                        conflict_columns=["symbol", "interval", "ts"],
                        update_columns=[
                            "sma_20",
                            "sma_50",
                            "ema_12",
                            "ema_26",
                            "rsi_14",
                            "macd",
                            "macd_signal",
                            "macd_hist",
                            "bb_upper",
                            "bb_middle",
                            "bb_lower",
                            "atr_14",
                            "obv",
                            "vwap",
                            "adx_14",
                            "metadata",
                        ],
                    )
                await session.commit()
            finally:
                PRICE_WRITE_LATENCY.observe(time.perf_counter() - start)

    async def run(self) -> None:
        symbols_env = os.getenv("TRACKED_COINS", "BTC,ETH,DOGE")
        symbols = [f"{item.strip().upper()}USDT" for item in symbols_env.split(",") if item.strip()]

        async def _historical_refresh_loop() -> None:
            while not self._shutdown.is_set():
                try:
                    await self._refresh_historical(symbols)
                except Exception as exc:
                    logger.exception("Historical refresh failed: %s", exc)
                await asyncio.sleep(self.historical_refresh_seconds)

        ws_task = asyncio.create_task(self.stream_realtime_prices(symbols))
        refresh_task = asyncio.create_task(_historical_refresh_loop())

        try:
            await asyncio.wait([ws_task, refresh_task], return_when=asyncio.FIRST_EXCEPTION)
        except asyncio.CancelledError:
            pass
        finally:
            ws_task.cancel()
            refresh_task.cancel()
            await self.shutdown()

    async def shutdown(self) -> None:
        self._shutdown.set()
        PRICE_WS_UP.set(0)

    def _parse_ws_tick(self, msg: Any) -> dict[str, Any] | None:
        if not msg:
            return None
        data = msg.get("data", msg)
        symbol = data.get("s")
        if not symbol:
            return None
        price = float(data.get("c", 0.0))
        volume = float(data.get("v", 0.0))
        quote_volume = float(data.get("q", 0.0))
        trade_count = int(data.get("n", 0))
        now = datetime.now(UTC)
        return {
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "quote_volume": quote_volume,
            "trade_count": trade_count,
            "ts": now,
        }

    async def _flush_cache(self) -> None:
        try:
            payload = {
                symbol: rows[-1] for symbol, rows in self._price_buffer.items() if rows
            }
            if not payload:
                return
            await db_manager.redis_client.set("prices:latest", json.dumps(payload, default=str), ex=30)
        except Exception as exc:
            logger.warning("Failed to flush price cache: %s", exc)

    async def _flush_db(self) -> None:
        rows: list[dict[str, Any]] = []
        for symbol, ticks in self._price_buffer.items():
            if not ticks:
                continue
            # aggregate ticks to one candle placeholder in 15m interval using latest tick
            latest = ticks[-1]
            price = float(latest["price"])
            rows.append(
                {
                    "symbol": symbol,
                    "interval": "15m",
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": float(latest.get("volume", 0.0)),
                    "quote_volume": float(latest.get("quote_volume", 0.0)),
                    "trade_count": int(latest.get("trade_count", 0)),
                    "source": "binance_ws",
                    "metadata": {"ingest": "realtime"},
                    "ts": latest["ts"],
+                    "created_at": datetime.now(UTC),
                }
            )
            ticks.clear()

        await self.save_price_data(rows)

    async def _refresh_historical(self, symbols: list[str]) -> None:
        for symbol in symbols:
            for interval in self.VALID_INTERVALS:
                try:
                    df = await self.fetch_historical_ohlcv(symbol, interval=interval, limit=1000)
                    if df is None or df.empty:
                        continue
                    df = await self.calculate_technical_indicators(df)
                    price_rows = self._df_to_price_rows(df)
                    ind_rows = self._df_to_indicator_rows(df)
                    await self.save_price_data(price_rows)
                    await self.save_technical_indicators(ind_rows)
                except Exception as exc:
                    logger.warning("Historical refresh failed for %s %s: %s", symbol, interval, exc)

    def _df_to_price_rows(self, df: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, item in df.iterrows():
            rows.append(
                {
                    "symbol": str(item["symbol"]),
                    "interval": str(item["interval"]),
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["volume"]),
                    "quote_volume": float(item.get("quote_volume", 0.0) or 0.0),
                    "trade_count": int(item.get("trade_count", 0) or 0),
                    "source": "binance_rest",
                    "metadata": {"ingest": "historical"},
                    "ts": item["ts"].to_pydatetime() if hasattr(item["ts"], "to_pydatetime") else item["ts"],
+                    "created_at": datetime.now(UTC),
                }
            )
        return rows

    def _df_to_indicator_rows(self, df: Any) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for _, item in df.iterrows():
            rows.append(
                {
                    "symbol": str(item["symbol"]),
                    "interval": str(item["interval"]),
                    "sma_20": self._to_float(item.get("sma_25")),
                    "sma_50": self._to_float(item.get("sma_99")),
                    "ema_12": self._to_float(item.get("ema_12")),
                    "ema_26": self._to_float(item.get("ema_26")),
                    "rsi_14": self._to_float(item.get("rsi_14")),
                    "macd": self._to_float(item.get("macd")),
                    "macd_signal": self._to_float(item.get("macd_signal")),
                    "macd_hist": self._to_float(item.get("macd_hist")),
                    "bb_upper": self._to_float(item.get("bb_upper")),
                    "bb_middle": self._to_float(item.get("bb_mid")),
                    "bb_lower": self._to_float(item.get("bb_lower")),
                    "atr_14": self._to_float(item.get("atr_14")),
                    "obv": self._to_float(item.get("obv")),
                    "vwap": self._to_float(item.get("vwap")),
                    "adx_14": self._to_float(item.get("adx_14")),
                    "metadata": {
                        "rsi_21": self._to_float(item.get("rsi_21")),
                        "ema_50": self._to_float(item.get("ema_50")),
                        "ema_200": self._to_float(item.get("ema_200")),
                        "stoch_k": self._to_float(item.get("stoch_k")),
                        "stoch_d": self._to_float(item.get("stoch_d")),
                    },
                    "ts": item["ts"].to_pydatetime() if hasattr(item["ts"], "to_pydatetime") else item["ts"],
+                    "created_at": datetime.now(UTC),
                }
            )
        return rows

    def _to_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            if self._pandas is not None and self._pandas.isna(value):
                return None
            return float(value)
        except Exception:
            return None
