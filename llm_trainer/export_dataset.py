from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import psycopg2


def build_news_prompt(title: str, content: str, symbol: str, source_name: str) -> str:
    return (
        "You are a crypto market analyst. Read the news item and return compact JSON with keys "
        "task, sentiment (BULLISH|BEARISH|NEUTRAL|FUD), confidence (0..1), and reasoning.\n\n"
        f"Source: {source_name}\n"
        f"Symbol: {symbol}\n"
        f"Title: {title.strip()}\n"
        f"Content: {content.strip()[:1800]}\n"
    )


def build_reddit_prompt(title: str, body: str, symbol: str, subreddit: str, score: int) -> str:
    return (
        "You are a crypto market analyst. Read this Reddit post and return compact JSON with keys "
        "task, sentiment (BULLISH|BEARISH|NEUTRAL|FUD), confidence (0..1), and reasoning.\n\n"
        f"Subreddit: {subreddit}\n"
        f"Score: {score}\n"
        f"Symbol: {symbol}\n"
        f"Title: {title.strip()}\n"
        f"Body: {body.strip()[:1800]}\n"
    )


def build_price_prompt(
    symbol: str,
    interval: str,
    ts: str,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    quote_volume: float,
    trade_count: int,
) -> str:
    return (
        "You are a crypto market analyst. Given the market candle, predict next-candle direction. "
        "Return compact JSON with keys task, direction (UP|DOWN|SIDEWAYS), confidence (0..1), "
        "expected_change_pct, and reasoning.\n\n"
        f"Symbol: {symbol}\n"
        f"Interval: {interval}\n"
        f"Timestamp: {ts}\n"
        f"Open: {open_price}\n"
        f"High: {high}\n"
        f"Low: {low}\n"
        f"Close: {close}\n"
        f"Volume: {volume}\n"
        f"Quote Volume: {quote_volume}\n"
        f"Trade Count: {trade_count}\n"
    )


def build_onchain_prompt(
    symbol: str,
    ts: str,
    amount: float,
    amount_usd: float,
    flow_direction: str,
    is_whale: bool,
) -> str:
    return (
        "You are a crypto market analyst. Assess this on-chain transfer and infer directional impact. "
        "Return compact JSON with keys task, impact (BULLISH|BEARISH|NEUTRAL), confidence (0..1), "
        "and reasoning.\n\n"
        f"Symbol: {symbol}\n"
        f"Timestamp: {ts}\n"
        f"Amount (coin): {amount}\n"
        f"Amount (USD): {amount_usd}\n"
        f"Flow Direction: {flow_direction}\n"
        f"Whale Transaction: {is_whale}\n"
    )


def heuristic_text_label(text: str) -> tuple[str, float, str]:
    lowered = text.lower()
    bullish = ["surge", "rally", "adoption", "approval", "partnership", "breakout"]
    bearish = ["hack", "exploit", "selloff", "ban", "lawsuit", "liquidation", "crash"]
    fud = ["rumor", "panic", "fear", "uncertainty", "insolvency"]

    if any(key in lowered for key in bearish):
        return ("BEARISH", 0.72, "Negative catalyst terms detected")
    if any(key in lowered for key in fud):
        return ("FUD", 0.70, "Fear/uncertainty terms detected")
    if any(key in lowered for key in bullish):
        return ("BULLISH", 0.72, "Positive catalyst terms detected")
    return ("NEUTRAL", 0.62, "No strong directional catalyst terms detected")


def next_candle_label(close: float, next_close: float) -> tuple[str, float, float, str]:
    if close <= 0:
        return ("SIDEWAYS", 0.55, 0.0, "Invalid close value; defaulting to neutral movement")

    change_pct = ((next_close - close) / close) * 100
    if change_pct > 0.25:
        direction = "UP"
    elif change_pct < -0.25:
        direction = "DOWN"
    else:
        direction = "SIDEWAYS"

    confidence = min(0.92, 0.56 + min(abs(change_pct), 8.0) / 20.0)
    reasoning = f"Observed next-candle move is {change_pct:.3f}%"
    return (direction, round(confidence, 4), round(change_pct, 6), reasoning)


def onchain_impact_label(flow_direction: str, is_whale: bool, amount_usd: float) -> tuple[str, float, str]:
    flow = (flow_direction or "wallet_to_wallet").strip().lower()
    if not is_whale and amount_usd < 250000:
        return ("NEUTRAL", 0.58, "Transaction size is below whale-impact thresholds")
    if flow == "to_exchange":
        return ("BEARISH", 0.78, "Large transfer to exchange can indicate potential sell pressure")
    if flow == "from_exchange":
        return ("BULLISH", 0.76, "Large withdrawal from exchange can indicate accumulation")
    return ("NEUTRAL", 0.66, "Wallet-to-wallet transfer has unclear immediate directional impact")


def _format_sft_record(prompt: str, target: dict[str, Any]) -> dict[str, str]:
    target_text = json.dumps(target, ensure_ascii=False)
    return {"text": f"<s>[INST] {prompt} [/INST] {target_text}</s>"}


def _to_text(value: Any) -> str:
    return "" if value is None else str(value)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_symbol(symbol: Any) -> str:
    value = _to_text(symbol).strip().upper()
    return value if value else "BTC"


def export_news_rows(cursor: Any, min_content_len: int, limit: int) -> list[dict[str, str]]:
    query = (
        "SELECT title, content, COALESCE(primary_symbol, 'BTC') AS symbol, COALESCE(source_name, 'unknown') "
        "FROM news_articles "
        "WHERE content IS NOT NULL AND LENGTH(content) >= %s "
        "ORDER BY ts DESC"
    )
    params: list[Any] = [min_content_len]
    if limit > 0:
        query += " LIMIT %s"
        params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    records: list[dict[str, str]] = []
    for title, content, symbol, source_name in rows:
        text = f"{_to_text(title)}\n{_to_text(content)}"
        label, confidence, reasoning = heuristic_text_label(text)
        prompt = build_news_prompt(
            title=_to_text(title),
            content=_to_text(content),
            symbol=_normalize_symbol(symbol),
            source_name=_to_text(source_name),
        )
        target = {
            "task": "news_sentiment",
            "sentiment": label,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        records.append(_format_sft_record(prompt, target))
    return records


def export_reddit_rows(cursor: Any, min_content_len: int, limit: int) -> list[dict[str, str]]:
    query = (
        "SELECT title, body, subreddit, score, mentioned_coins "
        "FROM reddit_posts "
        "WHERE LENGTH(COALESCE(title, '') || ' ' || COALESCE(body, '')) >= %s "
        "ORDER BY created_utc DESC"
    )
    params: list[Any] = [min_content_len]
    if limit > 0:
        query += " LIMIT %s"
        params.append(limit)

    cursor.execute(query, params)
    rows = cursor.fetchall()
    records: list[dict[str, str]] = []
    for title, body, subreddit, score, mentioned_coins in rows:
        coins = mentioned_coins if isinstance(mentioned_coins, list) else []
        symbol = _normalize_symbol(coins[0] if coins else "BTC")
        text = f"{_to_text(title)}\n{_to_text(body)}"
        label, confidence, reasoning = heuristic_text_label(text)
        prompt = build_reddit_prompt(
            title=_to_text(title),
            body=_to_text(body),
            symbol=symbol,
            subreddit=_to_text(subreddit),
            score=_to_int(score),
        )
        target = {
            "task": "reddit_sentiment",
            "sentiment": label,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        records.append(_format_sft_record(prompt, target))
    return records


def export_price_rows(cursor: Any, limit: int) -> list[dict[str, str]]:
    query = (
        "SELECT symbol, interval, ts, open, high, low, close, volume, quote_volume, trade_count, "
        "LEAD(close) OVER (PARTITION BY symbol, interval ORDER BY ts ASC) AS next_close "
        "FROM price_data "
        "ORDER BY symbol, interval, ts ASC"
    )
    if limit > 0:
        query += " LIMIT %s"
        cursor.execute(query, [limit])
    else:
        cursor.execute(query)

    rows = cursor.fetchall()
    records: list[dict[str, str]] = []
    for symbol, interval, ts, open_price, high, low, close, volume, quote_volume, trade_count, next_close in rows:
        close_value = _to_float(close)
        if next_close is None or close_value <= 0:
            continue
        next_close_value = _to_float(next_close)

        direction, confidence, change_pct, reasoning = next_candle_label(close_value, next_close_value)
        prompt = build_price_prompt(
            symbol=_normalize_symbol(symbol),
            interval=_to_text(interval),
            ts=_to_text(ts),
            open_price=_to_float(open_price),
            high=_to_float(high),
            low=_to_float(low),
            close=close_value,
            volume=_to_float(volume),
            quote_volume=_to_float(quote_volume),
            trade_count=_to_int(trade_count),
        )
        target = {
            "task": "next_candle_direction",
            "direction": direction,
            "confidence": confidence,
            "expected_change_pct": change_pct,
            "reasoning": reasoning,
        }
        records.append(_format_sft_record(prompt, target))
    return records


def export_onchain_rows(cursor: Any, limit: int) -> list[dict[str, str]]:
    query = (
        "SELECT ts, symbol, COALESCE(amount, 0), COALESCE(amount_usd, 0), "
        "COALESCE(flow_direction, 'wallet_to_wallet'), COALESCE(is_whale, FALSE) "
        "FROM onchain_transactions "
        "ORDER BY ts DESC"
    )
    if limit > 0:
        query += " LIMIT %s"
        cursor.execute(query, [limit])
    else:
        cursor.execute(query)

    rows = cursor.fetchall()
    records: list[dict[str, str]] = []
    for ts, symbol, amount, amount_usd, flow_direction, is_whale in rows:
        amount_usd_value = _to_float(amount_usd)
        impact, confidence, reasoning = onchain_impact_label(
            flow_direction=_to_text(flow_direction),
            is_whale=bool(is_whale),
            amount_usd=amount_usd_value,
        )
        prompt = build_onchain_prompt(
            symbol=_normalize_symbol(symbol),
            ts=_to_text(ts),
            amount=_to_float(amount),
            amount_usd=amount_usd_value,
            flow_direction=_to_text(flow_direction),
            is_whale=bool(is_whale),
        )
        target = {
            "task": "onchain_impact",
            "impact": impact,
            "confidence": confidence,
            "reasoning": reasoning,
        }
        records.append(_format_sft_record(prompt, target))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Export supervised fine-tuning dataset from BINFIN DB")
    parser.add_argument("--output", default="/workspace/artifacts/data/finance_news_sft.jsonl")
    parser.add_argument("--limit", type=int, default=15000, help="Row limit per text source table (news/reddit)")
    parser.add_argument("--price-limit", type=int, default=0, help="Row limit for price_data (0 = all)")
    parser.add_argument("--onchain-limit", type=int, default=0, help="Row limit for onchain_transactions (0 = all)")
    parser.add_argument("--min-content-len", type=int, default=80)
    parser.add_argument("--skip-news", action="store_true")
    parser.add_argument("--skip-reddit", action="store_true")
    parser.add_argument("--skip-price", action="store_true")
    parser.add_argument("--skip-onchain", action="store_true")
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL", "postgresql://binfin:binfin@postgres:5432/binfin")
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    conn = psycopg2.connect(db_url)
    cursor = conn.cursor()

    records: list[dict[str, str]] = []
    counts: dict[str, int] = {"news": 0, "reddit": 0, "price": 0, "onchain": 0}

    try:
        if not args.skip_news:
            news_records = export_news_rows(cursor, min_content_len=args.min_content_len, limit=args.limit)
            records.extend(news_records)
            counts["news"] = len(news_records)

        if not args.skip_reddit:
            reddit_records = export_reddit_rows(cursor, min_content_len=args.min_content_len, limit=args.limit)
            records.extend(reddit_records)
            counts["reddit"] = len(reddit_records)

        if not args.skip_price:
            price_records = export_price_rows(cursor, limit=args.price_limit)
            records.extend(price_records)
            counts["price"] = len(price_records)

        if not args.skip_onchain:
            onchain_records = export_onchain_rows(cursor, limit=args.onchain_limit)
            records.extend(onchain_records)
            counts["onchain"] = len(onchain_records)
    finally:
        cursor.close()
        conn.close()

    with output_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(
        f"Exported {len(records)} rows to {output_path} "
        f"(news={counts['news']}, reddit={counts['reddit']}, price={counts['price']}, onchain={counts['onchain']})"
    )


if __name__ == "__main__":
    main()
