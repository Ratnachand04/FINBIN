from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from config.settings import get_settings

logger = logging.getLogger(__name__)


class EtherscanCollector:
    BASE_URL = "https://api.etherscan.io/api"

    def __init__(self) -> None:
        self.settings = get_settings()
        self._last_calls: list[float] = []
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_ttl = 30
        self.known_whales: set[str] = set()

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        self._last_calls = [item for item in self._last_calls if now - item < 1]
        if len(self._last_calls) >= self.settings.etherscan_rate_limit_per_second:
            await asyncio.sleep(1 - (now - self._last_calls[0]))
        self._last_calls.append(time.monotonic())

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        cache_key = str(sorted(params.items()))
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]

        await self._rate_limit()
        params["apikey"] = self.settings.etherscan_api_key
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        self._cache[cache_key] = (now, payload)
        return payload

    async def fetch_whale_transactions(self, contract_address: str, threshold_usd: float = 1_000_000) -> list[dict[str, Any]]:
        payload = await self._get(
            {
                "module": "account",
                "action": "tokentx",
                "contractaddress": contract_address,
                "sort": "desc",
                "offset": 100,
                "page": 1,
            }
        )
        rows: list[dict[str, Any]] = []
        for tx in payload.get("result", []) or []:
            value = float(tx.get("value", 0.0) or 0.0)
            decimals = int(tx.get("tokenDecimal", "18") or 18)
            amount = value / (10 ** decimals)
            price = float(tx.get("tokenPriceUSD", 0.0) or 0.0)
            usd_value = amount * price
            if usd_value < threshold_usd:
                continue
            from_addr = str(tx.get("from", "")).lower()
            to_addr = str(tx.get("to", "")).lower()
            self.known_whales.update([from_addr, to_addr])
            rows.append(
                {
                    "tx_hash": str(tx.get("hash")),
                    "from_address": from_addr,
                    "to_address": to_addr,
                    "symbol": str(tx.get("tokenSymbol", "")),
                    "amount": amount,
                    "amount_usd": usd_value,
                    "ts": datetime.fromtimestamp(int(tx.get("timeStamp", "0")), tz=UTC),
                }
            )
        return rows

    async def calculate_exchange_flows(self, addresses: list[str], transactions: list[dict[str, Any]]) -> dict[str, float]:
        tracked = {item.lower() for item in addresses}
        inflow = 0.0
        outflow = 0.0
        for tx in transactions:
            if tx["to_address"] in tracked:
                inflow += float(tx.get("amount_usd", 0.0))
            if tx["from_address"] in tracked:
                outflow += float(tx.get("amount_usd", 0.0))
        return {"inflow": inflow, "outflow": outflow, "net": inflow - outflow}
