from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"
DEFAULT_ADDRESSES = [
    "0x3f5ce5fbfe3e9af3971dD833D26BA9b5C936f0bE",  # Binance
    "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance
    "0x503828976d22510aad0201ac7ec88293211d23da",  # Coinbase
    "0xddfabcdc4d8ffc6d5beaf154f18b778f892a0740",  # Coinbase
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2",  # Kraken
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Ethereum on-chain data from Etherscan into PostgreSQL SQL files."
    )
    parser.add_argument(
        "--addresses",
        nargs="+",
        default=DEFAULT_ADDRESSES,
        help="Ethereum addresses to crawl.",
    )
    parser.add_argument(
        "--output-prefix",
        default="database/eth_onchain_training_data_part",
        help="Output SQL file prefix.",
    )
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=120000,
        help="Maximum rows per SQL file.",
    )
    parser.add_argument(
        "--calls-per-second",
        type=int,
        default=int(os.getenv("ETHERSCAN_CALLS_PER_SECOND", "5")),
        help="Rate limit calls per second.",
    )
    parser.add_argument(
        "--max-calls",
        type=int,
        default=2000,
        help="Maximum total API calls for this run.",
    )
    parser.add_argument(
        "--start-block",
        type=int,
        default=0,
        help="Start block for Etherscan queries.",
    )
    parser.add_argument(
        "--block-window",
        type=int,
        default=500000,
        help="Initial block window size for range crawling.",
    )
    return parser.parse_args()


class RateLimiter:
    def __init__(self, calls_per_second: int) -> None:
        self.calls_per_second = max(1, calls_per_second)
        self.calls: deque[float] = deque(maxlen=200)

    def wait(self) -> None:
        now = time.monotonic()
        while self.calls and now - self.calls[0] > 1.0:
            self.calls.popleft()
        if len(self.calls) >= self.calls_per_second:
            sleep_for = 1.0 - (now - self.calls[0])
            if sleep_for > 0:
                time.sleep(sleep_for)
        self.calls.append(time.monotonic())


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def get_latest_block(client: httpx.Client, api_key: str, limiter: RateLimiter) -> int:
    limiter.wait()
    params = {
        "chainid": 1,
        "module": "proxy",
        "action": "eth_blockNumber",
        "apikey": api_key,
    }
    response = client.get(ETHERSCAN_API_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    raw = payload.get("result")
    if not isinstance(raw, str):
        raise RuntimeError("Unable to fetch latest block number from Etherscan")
    return int(raw, 16)


def fetch_range(
    client: httpx.Client,
    api_key: str,
    address: str,
    start_block: int,
    end_block: int,
    limiter: RateLimiter,
) -> tuple[list[dict[str, Any]], str]:
    limiter.wait()
    params = {
        "chainid": 1,
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": start_block,
        "endblock": end_block,
        "page": 1,
        "offset": 10000,
        "sort": "asc",
        "apikey": api_key,
    }
    response = client.get(ETHERSCAN_API_URL, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    if str(payload.get("status", "0")) == "0":
        message = str(payload.get("message", ""))
        result = payload.get("result")
        if isinstance(result, str) and result.lower() == "max rate limit reached":
            time.sleep(0.5)
            return [], "rate_limited"
        if isinstance(result, str) and "result window is too large" in result.lower():
            return [], "window_too_large"
        if message.lower() == "no transactions found":
            return [], "empty"
        return [], "empty"

    result = payload.get("result", [])
    if not isinstance(result, list):
        return [], "empty"
    return result, "ok"


def tx_to_insert_line(tx: dict[str, Any]) -> str | None:
    tx_hash = str(tx.get("hash", "")).strip()
    if not tx_hash:
        return None

    try:
        ts = datetime.fromtimestamp(int(tx.get("timeStamp", "0") or "0"), tz=UTC).strftime("%Y-%m-%d %H:%M:%S+00")
        block_number = int(tx.get("blockNumber", "0") or "0")
        from_address = str(tx.get("from", "")).lower()
        to_address = str(tx.get("to", "")).lower()
        value_wei = int(tx.get("value", "0") or "0")
    except Exception:
        return None

    amount_eth = value_wei / 1_000_000_000_000_000_000
    flow_direction = "wallet_to_wallet"

    metadata = {
        "source": "etherscan_txlist",
        "gas": tx.get("gas"),
        "gasPrice": tx.get("gasPrice"),
        "gasUsed": tx.get("gasUsed"),
        "isError": tx.get("isError"),
        "txreceipt_status": tx.get("txreceipt_status"),
        "nonce": tx.get("nonce"),
        "input": str(tx.get("input", ""))[:200],
    }

    values = [
        sql_value(ts),
        sql_value("ETH"),
        sql_value(tx_hash),
        sql_value(block_number),
        sql_value("ETH"),
        sql_value(from_address),
        sql_value(to_address),
        sql_value(amount_eth),
        sql_value(None),
        sql_value(False),
        sql_value(None),
        sql_value(flow_direction),
        sql_value(None),
        "ARRAY[]::TEXT[]",
        sql_value(json.dumps(metadata, separators=(",", ":"))),
    ]
    return f"({', '.join(values)})"


def write_sql_file(output_path: Path, insert_lines: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as file_handle:
        file_handle.write("-- Auto-generated Ethereum on-chain training data export\n")
        file_handle.write("-- Target table: onchain_transactions\n")
        file_handle.write("BEGIN;\n")
        file_handle.write(
            "CREATE TABLE IF NOT EXISTS onchain_transactions (\n"
            "    id BIGSERIAL PRIMARY KEY,\n"
            "    ts TIMESTAMPTZ NOT NULL,\n"
            "    chain TEXT NOT NULL,\n"
            "    tx_hash TEXT NOT NULL,\n"
            "    block_number BIGINT,\n"
            "    symbol TEXT NOT NULL,\n"
            "    from_address TEXT NOT NULL,\n"
            "    to_address TEXT NOT NULL,\n"
            "    amount NUMERIC(38, 18) NOT NULL,\n"
            "    amount_usd NUMERIC(38, 8),\n"
            "    is_whale BOOLEAN NOT NULL DEFAULT FALSE,\n"
            "    whale_threshold_usd NUMERIC(38, 8),\n"
            "    flow_direction TEXT CHECK (flow_direction IN ('to_exchange', 'from_exchange', 'wallet_to_wallet')),\n"
            "    exchange_name TEXT,\n"
            "    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],\n"
            "    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,\n"
            "    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n"
            "    UNIQUE (chain, tx_hash)\n"
            ");\n"
        )

        if insert_lines:
            file_handle.write(
                "INSERT INTO onchain_transactions (\n"
                "    ts, chain, tx_hash, block_number, symbol, from_address, to_address, amount,\n"
                "    amount_usd, is_whale, whale_threshold_usd, flow_direction, exchange_name, tags, metadata\n"
                ") VALUES\n"
            )
            file_handle.write(",\n".join(insert_lines))
            file_handle.write(
                "\nON CONFLICT (chain, tx_hash) DO UPDATE SET\n"
                "    block_number = EXCLUDED.block_number,\n"
                "    amount = EXCLUDED.amount,\n"
                "    flow_direction = EXCLUDED.flow_direction,\n"
                "    metadata = EXCLUDED.metadata;\n"
            )

        file_handle.write("COMMIT;\n")


def write_sql_files(prefix: str, rows: list[str], rows_per_file: int) -> list[Path]:
    if rows_per_file <= 0:
        raise ValueError("rows_per_file must be > 0")

    paths: list[Path] = []
    index = 1
    for start in range(0, len(rows), rows_per_file):
        part = rows[start : start + rows_per_file]
        path = Path(f"{prefix}_{index:03d}.sql")
        write_sql_file(path, part)
        paths.append(path)
        index += 1

    if not paths:
        path = Path(f"{prefix}_001.sql")
        write_sql_file(path, [])
        paths.append(path)

    return paths


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("ETHERSCAN_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ETHERSCAN_API_KEY is required")

    limiter = RateLimiter(args.calls_per_second)
    api_calls = 0
    insert_lines: list[str] = []

    with httpx.Client() as client:
        latest_block = get_latest_block(client, api_key, limiter)
        print(f"Latest ETH block: {latest_block}")

        for address in [a.strip() for a in args.addresses if a.strip()]:
            current_start = args.start_block
            window = max(1000, args.block_window)
            while api_calls < args.max_calls and current_start <= latest_block:
                current_end = min(current_start + window - 1, latest_block)
                rows, status = fetch_range(
                    client=client,
                    api_key=api_key,
                    address=address,
                    start_block=current_start,
                    end_block=current_end,
                    limiter=limiter,
                )
                api_calls += 1

                if status == "window_too_large":
                    window = max(1000, window // 2)
                    print(
                        f"Address {address} blocks {current_start}-{current_end}: window too large, reducing window to {window}"
                    )
                    continue
                if status == "rate_limited":
                    continue
                if not rows:
                    current_start = current_end + 1
                    continue

                converted = 0
                for tx in rows:
                    line = tx_to_insert_line(tx)
                    if line:
                        insert_lines.append(line)
                        converted += 1

                print(
                    f"Address {address} blocks {current_start}-{current_end}: fetched={len(rows)} converted={converted}"
                )

                if len(rows) >= 10000 and window > 1000:
                    window = max(1000, window // 2)
                elif len(rows) < 2500 and window < args.block_window:
                    window = min(args.block_window, window * 2)

                current_start = current_end + 1

            if api_calls >= args.max_calls:
                print("Reached max-calls limit, stopping early.")
                break

    paths = write_sql_files(args.output_prefix, insert_lines, args.rows_per_file)

    print(f"Total API calls: {api_calls}")
    print(f"Total rows exported: {len(insert_lines)}")
    print("Output files:")
    for path in paths:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
