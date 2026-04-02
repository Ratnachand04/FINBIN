from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from typing import TypeVar

import httpx
from dotenv import load_dotenv

BASE_URL = "https://newsdata.io/api/1/news"
T = TypeVar("T")
DEFAULT_QUERIES = [
    "bitcoin OR BTC",
    "ethereum OR ETH",
    "dogecoin OR DOGE",
    "crypto market",
    "blockchain regulation",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export NewsData.io results to PostgreSQL SQL inserts for news_articles."
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        default=DEFAULT_QUERIES,
        help="NewsData queries to execute",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=500,
        help="Maximum NewsData API requests to send in this run",
    )
    parser.add_argument(
        "--sleep-ms",
        type=int,
        default=250,
        help="Delay between API requests in milliseconds",
    )
    parser.add_argument(
        "--rows-per-file",
        type=int,
        default=25000,
        help="Max number of INSERT rows per output SQL file",
    )
    parser.add_argument(
        "--output-prefix",
        default="database/newsdata_articles",
        help="Output SQL filename prefix",
    )
    return parser.parse_args()


def sql_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def detect_coins(text: str) -> tuple[list[str], str | None]:
    t = (text or "").upper()
    mentions: list[str] = []
    if any(x in t for x in ["BTC", "BITCOIN"]):
        mentions.append("BTC")
    if any(x in t for x in ["ETH", "ETHEREUM", "ETHER"]):
        mentions.append("ETH")
    if any(x in t for x in ["DOGE", "DOGECOIN"]):
        mentions.append("DOGE")

    primary: str | None = None
    for symbol in ["BTC", "ETH", "DOGE"]:
        if symbol in mentions:
            primary = symbol
            break

    deduped = sorted(set(mentions), key=lambda s: ["BTC", "ETH", "DOGE"].index(s))
    return deduped, primary


def to_row(item: dict[str, Any], query: str) -> dict[str, Any]:
    title = (item.get("title") or "").strip()
    content = (item.get("content") or item.get("description") or "").strip()
    full_text = f"{title} {content}".strip()
    mentions, primary = detect_coins(full_text)

    link = (
        item.get("link")
        or item.get("url")
        or item.get("source_url")
        or ""
    ).strip()

    if not link:
        # Keep deterministic dedupe fallback if url missing.
        link = f"newsdata://{sha256((title + content + query).encode('utf-8')).hexdigest()}"

    published_at = parse_datetime(item.get("pubDate") or item.get("publishedAt"))
    ts = published_at.strftime("%Y-%m-%d %H:%M:%S+00")

    source_name = (
        item.get("source_name")
        or item.get("source_id")
        or item.get("source")
        or "newsdata.io"
    )

    metadata = {
        "source": "newsdata_io",
        "query": query,
        "category": item.get("category"),
        "country": item.get("country"),
        "language": item.get("language"),
        "creator": item.get("creator"),
        "image_url": item.get("image_url"),
        "video_url": item.get("video_url"),
        "keywords": item.get("keywords"),
    }

    return {
        "ts": ts,
        "url_hash": sha256(link.encode("utf-8")).hexdigest(),
        "source_name": str(source_name)[:250] if source_name else "newsdata.io",
        "title": title if title else "(untitled)",
        "content": content,
        "author": ", ".join(item.get("creator") or []) if isinstance(item.get("creator"), list) else item.get("creator"),
        "url": link,
        "image_url": item.get("image_url"),
        "mentioned_coins": mentions,
        "primary_symbol": primary,
        "sentiment_keywords": [],
        "metadata": metadata,
    }


def row_to_values_sql(row: dict[str, Any]) -> str:
    mentions_sql = "ARRAY[]::TEXT[]"
    if row["mentioned_coins"]:
        arr = ", ".join(sql_value(c) for c in row["mentioned_coins"])
        mentions_sql = f"ARRAY[{arr}]::TEXT[]"

    keywords_sql = "ARRAY[]::TEXT[]"
    if row["sentiment_keywords"]:
        arr = ", ".join(sql_value(c) for c in row["sentiment_keywords"])
        keywords_sql = f"ARRAY[{arr}]::TEXT[]"

    metadata_json = json.dumps(row["metadata"], ensure_ascii=True, separators=(",", ":"))

    cols = [
        sql_value(row["ts"]),
        sql_value(row["url_hash"]),
        sql_value(row["source_name"]),
        sql_value(row["title"]),
        sql_value(row["content"]),
        sql_value(row["author"]),
        sql_value(row["url"]),
        sql_value(row["image_url"]),
        mentions_sql,
        sql_value(row["primary_symbol"]),
        keywords_sql,
        sql_value(metadata_json) + "::jsonb",
    ]
    return f"({', '.join(cols)})"


def write_sql_file(path: Path, values_sql: list[str], total_rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write("-- Auto-generated NewsData.io news export\n")
        f.write(f"-- Rows in this file: {len(values_sql)}\n")
        f.write(f"-- Total rows in run: {total_rows}\n")
        f.write("-- Target table: news_articles (see database/schema.sql)\n")
        f.write("BEGIN;\n")
        if values_sql:
            f.write(
                "INSERT INTO news_articles (\n"
                "  ts, url_hash, source_name, title, content, author, url, image_url,\n"
                "  mentioned_coins, primary_symbol, sentiment_keywords, metadata\n"
                ") VALUES\n"
            )
            f.write(",\n".join(values_sql))
            f.write(
                "\nON CONFLICT (url_hash) DO UPDATE SET\n"
                "  title = EXCLUDED.title,\n"
                "  content = EXCLUDED.content,\n"
                "  author = EXCLUDED.author,\n"
                "  url = EXCLUDED.url,\n"
                "  image_url = EXCLUDED.image_url,\n"
                "  source_name = EXCLUDED.source_name,\n"
                "  mentioned_coins = EXCLUDED.mentioned_coins,\n"
                "  primary_symbol = EXCLUDED.primary_symbol,\n"
                "  sentiment_keywords = EXCLUDED.sentiment_keywords,\n"
                "  metadata = EXCLUDED.metadata;\n"
            )
        f.write("COMMIT;\n")


def chunked(items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def main() -> None:
    load_dotenv()
    args = parse_args()

    api_key = (
        os.getenv("NEWSDATA_API_KEY", "").strip()
        or os.getenv("NEWDATA_API_KEY", "").strip()
    )
    if not api_key:
        raise SystemExit("Missing NEWSDATA_API_KEY (or NEWDATA_API_KEY) in environment/.env")

    if args.max_requests <= 0:
        raise SystemExit("--max-requests must be > 0")
    if args.rows_per_file <= 0:
        raise SystemExit("--rows-per-file must be > 0")

    rows: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    requests_used = 0

    with httpx.Client(timeout=40) as client:
        for query in args.queries:
            next_page: str | None = None
            while requests_used < args.max_requests:
                params: dict[str, Any] = {
                    "apikey": api_key,
                    "q": query,
                    "language": "en",
                    "size": 10,
                }
                if next_page:
                    params["page"] = next_page

                response = client.get(BASE_URL, params=params)
                requests_used += 1

                if response.status_code == 429:
                    print("Rate limit reached from NewsData.io; stopping collection.")
                    break
                response.raise_for_status()

                payload = response.json()
                status = str(payload.get("status", "")).lower()
                if status not in {"success", "ok"}:
                    print(f"NewsData returned non-success status for query '{query}': {status}")
                    break

                batch = payload.get("results") or []
                if not batch:
                    break

                for item in batch:
                    row = to_row(item, query=query)
                    if row["url_hash"] in seen_hashes:
                        continue
                    seen_hashes.add(row["url_hash"])
                    rows.append(row)

                next_page = payload.get("nextPage")
                if not next_page:
                    break

                time.sleep(max(0, args.sleep_ms) / 1000)

            if requests_used >= args.max_requests:
                print("Reached --max-requests limit; stopping collection.")
                break

    values = [row_to_values_sql(r) for r in rows]

    out_prefix = Path(args.output_prefix)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    if not values:
        out_path = Path(f"{out_prefix}_{timestamp}_001.sql")
        write_sql_file(out_path, [], 0)
        print(f"No rows collected. Wrote empty SQL transaction file: {out_path}")
        return

    parts = chunked(values, args.rows_per_file)
    output_files: list[Path] = []
    for index, part in enumerate(parts, start=1):
        out_path = Path(f"{out_prefix}_{timestamp}_{index:03d}.sql")
        write_sql_file(out_path, part, len(values))
        output_files.append(out_path)

    print(f"Collected unique rows: {len(values)}")
    print(f"Requests used: {requests_used}")
    print("SQL files generated:")
    for p in output_files:
        print(f"  - {p}")


if __name__ == "__main__":
    main()
