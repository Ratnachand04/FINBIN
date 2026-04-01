from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from prometheus_client import Counter, Gauge
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.database import bulk_insert, db_manager, execute_raw_sql, upsert

logger = logging.getLogger(__name__)

ONCHAIN_TX_COLLECTED = Counter("binfin_onchain_tx_collected_total", "Total onchain transactions collected")
ONCHAIN_COLLECTOR_UP = Gauge("binfin_onchain_collector_up", "Onchain collector health status (1=up, 0=down)")
ONCHAIN_ANOMALIES = Counter("binfin_onchain_anomaly_total", "Detected unusual volume anomalies")


class OnchainCollector:
    """Multi-chain onchain collector for ETH/BSC/SOL with caching and anomaly detection."""

    ETHERSCAN_API = "https://api.etherscan.io/api"
    BSCSCAN_API = "https://api.bscscan.com/api"
    SOLANA_RPC = "https://api.mainnet-beta.solana.com"

    KNOWN_EXCHANGES: dict[str, set[str]] = {
        "BINANCE": {
            "0x3f5ce5fbfe3e9af3971dD833D26BA9b5C936f0bE".lower(),
            "0x28C6c06298d514Db089934071355E5743bf21d60".lower(),
        },
        "COINBASE": {
            "0x503828976d22510aad0201ac7ec88293211d23da".lower(),
            "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740".lower(),
        },
        "KRAKEN": {
            "0x2910543af39aba0cd09dbb2d50200b3e800a63d2".lower(),
        },
    }

    def __init__(self) -> None:
        self.etherscan_api_key = os.getenv("ETHERSCAN_API_KEY", "")
        self.bscscan_api_key = os.getenv("BSCSCAN_API_KEY", self.etherscan_api_key)
        self.calls_per_second = 5
        self._call_timestamps: deque[float] = deque(maxlen=100)
        self._shutdown = asyncio.Event()

    async def __aenter__(self) -> "OnchainCollector":
        ONCHAIN_COLLECTOR_UP.set(1)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.shutdown()

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        while self._call_timestamps and now - self._call_timestamps[0] > 1:
            self._call_timestamps.popleft()
        if len(self._call_timestamps) >= self.calls_per_second:
            wait = 1 - (now - self._call_timestamps[0])
            if wait > 0:
                await asyncio.sleep(wait)
        self._call_timestamps.append(time.monotonic())

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        for attempt in range(5):
            try:
                await self._rate_limit()
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                delay = min(2**attempt, 20)
                logger.warning("Onchain API call failed (%s), retry in %ss", exc, delay)
                await asyncio.sleep(delay)
        raise RuntimeError(f"Failed API request for {url}")

    async def get_whale_transactions(
        self,
        token_address: str,
        min_value_usd: float = 1_000_000,
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for chain in ("ETH", "BSC", "SOL"):
            chain_txs = await self._fetch_chain_transactions(chain, token_address)
            for tx in chain_txs:
                usd_value = await self.calculate_value_usd(
                    amount=float(tx.get("amount", 0)),
                    token=tx.get("symbol", ""),
                    timestamp=tx.get("ts"),
                )
                if usd_value < min_value_usd:
                    continue

                from_exchange = await self.identify_exchange(tx.get("from_address", ""))
                to_exchange = await self.identify_exchange(tx.get("to_address", ""))
                flow_direction = self._classify_flow(from_exchange, to_exchange)

                tx.update(
                    {
                        "amount_usd": usd_value,
                        "is_whale": True,
                        "whale_threshold_usd": min_value_usd,
                        "flow_direction": flow_direction,
                        "exchange_name": to_exchange or from_exchange,
                    }
                )
                records.append(tx)

        return records

    async def track_top_holders(self, token_address: str, top_n: int = 100) -> list[dict[str, Any]]:
        holders = await self._fetch_top_holders(token_address, top_n=top_n)
        previous = await self._load_previous_holders(token_address)

        trends: list[dict[str, Any]] = []
        for item in holders:
            address = item["address"]
            current_balance = item["balance"]
            previous_balance = previous.get(address, 0.0)
            change = current_balance - previous_balance
            trend = "accumulation" if change > 0 else "distribution" if change < 0 else "flat"
            trends.append(
                {
                    "token_address": token_address,
                    "address": address,
                    "balance": current_balance,
                    "previous_balance": previous_balance,
                    "change": change,
                    "pattern": trend,
                    "ts": datetime.now(UTC),
                }
            )

        await self._store_holder_snapshot(token_address, holders)
        return trends

    async def get_exchange_flows(self, exchange_addresses: list[str]) -> dict[str, Any]:
        now = datetime.now(UTC)
        windows = {"1h": now - timedelta(hours=1), "24h": now - timedelta(hours=24)}
        aggregate: dict[str, Any] = {"windows": {}, "anomalies": []}

        async with db_manager.session_factory() as session:
            for label, cutoff in windows.items():
                query = (
                    "SELECT COALESCE(SUM(CASE WHEN flow_direction = 'to_exchange' THEN amount_usd ELSE 0 END),0) AS inflow, "
                    "COALESCE(SUM(CASE WHEN flow_direction = 'from_exchange' THEN amount_usd ELSE 0 END),0) AS outflow "
                    "FROM onchain_transactions WHERE ts >= :cutoff AND "
                    "(lower(from_address) = ANY(:addresses) OR lower(to_address) = ANY(:addresses))"
                )
                result = await execute_raw_sql(
                    session,
                    query,
                    {"cutoff": cutoff, "addresses": [addr.lower() for addr in exchange_addresses]},
                )
                row = result.first()
                inflow = float(row.inflow if row else 0)
                outflow = float(row.outflow if row else 0)
                net = inflow - outflow
                aggregate["windows"][label] = {
                    "inflow": inflow,
                    "outflow": outflow,
                    "net": net,
                }

                anomaly = self._detect_unusual_flow(label, inflow, outflow)
                if anomaly:
                    aggregate["anomalies"].append(anomaly)
                    ONCHAIN_ANOMALIES.inc()

        return aggregate

    async def identify_exchange(self, address: str) -> str | None:
        address_norm = address.lower().strip()
        if not address_norm:
            return None

        cache_key = f"onchain:exchange:{address_norm}"
        try:
            cached = await db_manager.redis_client.get(cache_key)
            if cached:
                return cached
        except Exception:
            pass

        label = None
        for exchange, addresses in self.KNOWN_EXCHANGES.items():
            if address_norm in addresses:
                label = exchange
                break

        if label:
            try:
                await db_manager.redis_client.set(cache_key, label, ex=86400)
            except Exception:
                pass
        return label

    async def calculate_value_usd(self, amount: float, token: str, timestamp: datetime | str | None) -> float:
        if amount <= 0:
            return 0.0

        price = await self._get_historical_price(token, timestamp)
        return amount * price

    async def save_to_db(self, transactions: list[dict[str, Any]]) -> None:
        if not transactions:
            return

        async with db_manager.session_factory() as session:
            try:
                try:
                    await bulk_insert(session, "onchain_transactions", transactions)
                except IntegrityError:
                    for tx in transactions:
                        await upsert(
                            session=session,
                            table_name="onchain_transactions",
                            values=tx,
                            conflict_columns=["chain", "tx_hash"],
                            update_columns=[
                                "amount_usd",
                                "is_whale",
                                "flow_direction",
                                "exchange_name",
                                "metadata",
                            ],
                        )

                await session.commit()
                ONCHAIN_TX_COLLECTED.inc(len(transactions))
            except SQLAlchemyError as exc:
                await session.rollback()
                logger.exception("Failed saving onchain transactions: %s", exc)
                raise

        await self._update_aggregates(transactions)

    async def shutdown(self) -> None:
        self._shutdown.set()
        ONCHAIN_COLLECTOR_UP.set(0)

    async def _fetch_chain_transactions(self, chain: str, token_address: str) -> list[dict[str, Any]]:
        if chain in {"ETH", "BSC"}:
            return await self._fetch_evm_transactions(chain, token_address)
        if chain == "SOL":
            return await self._fetch_solana_transactions(token_address)
        return []

    async def _fetch_evm_transactions(self, chain: str, token_address: str) -> list[dict[str, Any]]:
        api_url = self.ETHERSCAN_API if chain == "ETH" else self.BSCSCAN_API
        api_key = self.etherscan_api_key if chain == "ETH" else self.bscscan_api_key
        params = {
            "module": "account",
            "action": "tokentx",
            "contractaddress": token_address,
            "sort": "desc",
            "page": 1,
            "offset": 200,
            "apikey": api_key,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            payload = await self._request_with_retry(client, "GET", api_url, params=params)

        rows: list[dict[str, Any]] = []
        for tx in payload.get("result", []) or []:
            decimals = int(tx.get("tokenDecimal", "18") or "18")
            raw_value = float(tx.get("value", "0") or "0")
            amount = raw_value / (10**decimals)
            ts = datetime.fromtimestamp(int(tx.get("timeStamp", "0") or "0"), tz=UTC)
            tx_hash = str(tx.get("hash", ""))
            if not tx_hash:
                tx_hash = hashlib.sha256(f"{chain}:{token_address}:{ts.timestamp()}:{amount}".encode()).hexdigest()

            rows.append(
                {
                    "ts": ts,
                    "chain": chain,
                    "tx_hash": tx_hash,
                    "block_number": int(tx.get("blockNumber", "0") or "0"),
                    "symbol": str(tx.get("tokenSymbol", "UNK") or "UNK"),
                    "from_address": str(tx.get("from", "")),
                    "to_address": str(tx.get("to", "")),
                    "amount": amount,
                    "amount_usd": 0.0,
                    "is_whale": False,
                    "whale_threshold_usd": None,
                    "flow_direction": None,
                    "exchange_name": None,
                    "tags": [],
                    "metadata": {"source": "etherscan" if chain == "ETH" else "bscscan"},
                    "created_at": datetime.now(UTC),
                }
            )
        return rows

    async def _fetch_solana_transactions(self, token_address: str) -> list[dict[str, Any]]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [token_address, {"limit": 100}],
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._request_with_retry(client, "POST", self.SOLANA_RPC, json=payload)

        rows: list[dict[str, Any]] = []
        for item in response.get("result", []) or []:
            block_time = item.get("blockTime")
            if not block_time:
                continue
            ts = datetime.fromtimestamp(int(block_time), tz=UTC)
            signature = str(item.get("signature", ""))
            rows.append(
                {
                    "ts": ts,
                    "chain": "SOL",
                    "tx_hash": signature,
                    "block_number": None,
                    "symbol": "SOL",
                    "from_address": "",
                    "to_address": token_address,
                    "amount": 0.0,
                    "amount_usd": 0.0,
                    "is_whale": False,
                    "whale_threshold_usd": None,
                    "flow_direction": None,
                    "exchange_name": None,
                    "tags": ["solana_signature_only"],
                    "metadata": {"source": "solana_rpc"},
                    "created_at": datetime.now(UTC),
                }
            )
        return rows

    async def _fetch_top_holders(self, token_address: str, top_n: int = 100) -> list[dict[str, Any]]:
        # Placeholder for chain-specific holder API integration.
        # Kept deterministic and typed for service orchestration.
        holders: list[dict[str, Any]] = []
        for idx in range(top_n):
            holders.append(
                {
                    "address": f"holder_{idx}_{token_address[:8]}",
                    "balance": max(0.0, float(top_n - idx) * 1000.0),
                }
            )
        return holders

    async def _load_previous_holders(self, token_address: str) -> dict[str, float]:
        key = f"onchain:holders:{token_address.lower()}"
        try:
            payload = await db_manager.redis_client.hgetall(key)
            return {addr: float(balance) for addr, balance in payload.items()}
        except Exception:
            return {}

    async def _store_holder_snapshot(self, token_address: str, holders: list[dict[str, Any]]) -> None:
        key = f"onchain:holders:{token_address.lower()}"
        mapping = {item["address"]: str(item["balance"]) for item in holders}
        try:
            if mapping:
                await db_manager.redis_client.hset(key, mapping=mapping)
                await db_manager.redis_client.expire(key, 86400)
        except Exception as exc:
            logger.warning("Unable to cache holder snapshot: %s", exc)

    def _classify_flow(self, from_exchange: str | None, to_exchange: str | None) -> str:
        if to_exchange and not from_exchange:
            return "to_exchange"
        if from_exchange and not to_exchange:
            return "from_exchange"
        return "wallet_to_wallet"

    def _detect_unusual_flow(self, window: str, inflow: float, outflow: float) -> dict[str, Any] | None:
        gross = inflow + outflow
        threshold = 5_000_000 if window == "1h" else 25_000_000
        if gross > threshold:
            return {
                "window": window,
                "inflow": inflow,
                "outflow": outflow,
                "gross": gross,
                "threshold": threshold,
                "detected_at": datetime.now(UTC).isoformat(),
            }
        return None

    async def _get_historical_price(self, token: str, timestamp: datetime | str | None) -> float:
        symbol = (token or "").upper()
        if not symbol:
            return 0.0

        ts = self._coerce_timestamp(timestamp)
        async with db_manager.session_factory() as session:
            query = (
                "SELECT close FROM price_data WHERE symbol = :symbol AND ts <= :ts "
                "ORDER BY ts DESC LIMIT 1"
            )
            result = await execute_raw_sql(session, query, {"symbol": symbol, "ts": ts})
            row = result.first()
            if row and row.close is not None:
                return float(row.close)

        return await self._fallback_spot_price(symbol)

    async def _fallback_spot_price(self, symbol: str) -> float:
        coin_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "BNB": "binancecoin",
        }
        coin_id = coin_map.get(symbol)
        if not coin_id:
            return 0.0

        endpoint = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": coin_id, "vs_currencies": "usd"}
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                payload = await self._request_with_retry(client, "GET", endpoint, params=params)
                return float(payload.get(coin_id, {}).get("usd", 0.0))
            except Exception:
                return 0.0

    def _coerce_timestamp(self, timestamp: datetime | str | None) -> datetime:
        if isinstance(timestamp, datetime):
            return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        if isinstance(timestamp, str):
            try:
                if timestamp.endswith("Z"):
                    timestamp = timestamp[:-1] + "+00:00"
                parsed = datetime.fromisoformat(timestamp)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except Exception:
                pass
        return datetime.now(UTC)

    async def _update_aggregates(self, transactions: list[dict[str, Any]]) -> None:
        if not transactions:
            return
        total_usd = sum(float(tx.get("amount_usd", 0) or 0) for tx in transactions)
        payload = {
            "count": len(transactions),
            "total_usd": total_usd,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        try:
            await db_manager.redis_client.set("onchain:last_aggregate", str(payload), ex=300)
        except Exception as exc:
            logger.warning("Unable to cache onchain aggregate payload: %s", exc)