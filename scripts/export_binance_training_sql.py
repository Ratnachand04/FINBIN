from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import httpx
from dotenv import load_dotenv

BASE_URL = os.getenv("BINANCE_REST_URL", "https://api.binance.com")
MAX_LIMIT = 1000
DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DEFAULT_INTERVALS = ["15m", "1h", "4h", "1d"]
EARLIEST_OPEN_TIME_MS = 1502942400000  # First Binance spot candles in Aug 2017


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Binance historical OHLCV into PostgreSQL SQL inserts."
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=DEFAULT_SYMBOLS,
        help="Symbols to export (default: BTCUSDT ETHUSDT)",
    )
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=DEFAULT_INTERVALS,
        help="Intervals to export (default: 15m 1h 4h 1d)",
    )
    parser.add_argument(
        "--output-prefix",
        default="database/btc_eth_training_data_part",
        help="Output SQL file prefix",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=120,
        help="Delay between requests in milliseconds",
    )
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=200000,
        help="Maximum INSERT rows per SQL file",
    )
    return parser.parse_args()


def fetch_klines(
    client: httpx.Client,
    symbol: str,
    interval: str,
    start_time_ms: int,
    sleep_ms: int,
) -> list[list]:
    rows: list[list] = []
    cursor = start_time_ms

    while True:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": cursor,
            "limit": MAX_LIMIT,
        }
        response = client.get(f"{BASE_URL}/api/v3/klines", params=params)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break

        rows.extend(batch)
        last_open_time = int(batch[-1][0])
        next_cursor = last_open_time + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor

        if len(batch) < MAX_LIMIT:
            break

        time.sleep(max(0, sleep_ms) / 1000)

    return rows


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def to_insert_lines(symbol: str, interval: str, klines: Iterable[list]) -> list[str]:
    lines: list[str] = []
    for k in klines:
        open_time = int(k[0])
        open_ts = datetime.fromtimestamp(open_time / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S+00")

        metadata = {
            "close_time": int(k[6]),
            "trade_count": int(k[8]),
            "taker_buy_base": float(k[9]),
            "taker_buy_quote": float(k[10]),
            "source": "binance_rest_klines",
        }

        values = [
            sql_value(open_ts),
            sql_value(symbol),
            sql_value(interval),
            sql_value(float(k[1])),
            sql_value(float(k[2])),
            sql_value(float(k[3])),
            sql_value(float(k[4])),
            sql_value(float(k[5])),
            sql_value(float(k[7])),
            sql_value(json.dumps(metadata, separators=(",", ":"))),
        ]
        lines.append(f"({', '.join(values)})")

    return lines


def write_sql_file(output_path: Path, inserts: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Auto-generated Binance BTC/ETH training data export\n")
        f.write("-- Target table: price_data (see database/schema.sql)\n")
        f.write("BEGIN;\n")
        f.write(
            "CREATE TABLE IF NOT EXISTS price_data (\n"
            "    id BIGSERIAL PRIMARY KEY,\n"
            "    ts TIMESTAMPTZ NOT NULL,\n"
            "    symbol TEXT NOT NULL,\n"
            "    interval TEXT NOT NULL,\n"
            "    open DOUBLE PRECISION NOT NULL,\n"
            "    high DOUBLE PRECISION NOT NULL,\n"
            "    low DOUBLE PRECISION NOT NULL,\n"
            "    close DOUBLE PRECISION NOT NULL,\n"
            "    volume DOUBLE PRECISION NOT NULL,\n"
            "    quote_volume DOUBLE PRECISION,\n"
            "    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,\n"
            "    UNIQUE(symbol, interval, ts)\n"
            ");\n"
        )

        if inserts:
            f.write(
                "INSERT INTO price_data (\n"
                "    ts, symbol, interval, open, high, low, close, volume, quote_volume, metadata\n"
                ") VALUES\n"
            )
            f.write(",\n".join(inserts))
            f.write(
                "\nON CONFLICT(symbol, interval, ts) DO UPDATE SET\n"
                "    open = EXCLUDED.open,\n"
                "    high = EXCLUDED.high,\n"
                "    low = EXCLUDED.low,\n"
                "    close = EXCLUDED.close,\n"
                "    volume = EXCLUDED.volume,\n"
                "    quote_volume = EXCLUDED.quote_volume,\n"
                "    metadata = EXCLUDED.metadata;\n"
            )

        f.write("COMMIT;\n")


def write_sql_files(output_prefix: str, inserts: list[str], rows_per_file: int) -> list[Path]:
    if rows_per_file <= 0:
        raise ValueError("rows_per_file must be > 0")

    output_paths: list[Path] = []
    if not inserts:
        empty_path = Path(f"{output_prefix}_001.sql")
        write_sql_file(empty_path, [])
        return [empty_path]

    chunk_index = 1
    for start in range(0, len(inserts), rows_per_file):
        chunk = inserts[start : start + rows_per_file]
        output_path = Path(f"{output_prefix}_{chunk_index:03d}.sql")
        write_sql_file(output_path, chunk)
        output_paths.append(output_path)
        chunk_index += 1

    return output_paths


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    headers = {"X-MBX-APIKEY": api_key} if api_key else {}

    all_inserts: list[str] = []
    with httpx.Client(timeout=30, headers=headers) as client:
        for symbol in [s.upper().strip() for s in args.symbols if s.strip()]:
            for interval in [i.strip() for i in args.intervals if i.strip()]:
                print(f"Fetching {symbol} {interval} ...")
                klines = fetch_klines(
                    client=client,
                    symbol=symbol,
                    interval=interval,
                    start_time_ms=EARLIEST_OPEN_TIME_MS,
                    sleep_ms=args.sleep_ms,
                )
                print(f"  {len(klines)} rows")
                all_inserts.extend(to_insert_lines(symbol=symbol, interval=interval, klines=klines))

    output_paths = write_sql_files(
        output_prefix=args.output_prefix,
        inserts=all_inserts,
        rows_per_file=args.rows_per_file,
    )
    print(f"Wrote {len(all_inserts)} rows across {len(output_paths)} files:")
    for path in output_paths:
        print(f"  - {path}")


if __name__ == "__main__":
    main()
