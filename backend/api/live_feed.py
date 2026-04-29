from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.database import db_session_context, execute_raw_sql

router = APIRouter(prefix="/api/v1/live", tags=["live-feed"])


async def _table_exists(table_name: str) -> bool:
    async with db_session_context() as session:
        row = (
            await execute_raw_sql(
                session,
                "SELECT EXISTS ("
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :table_name"
                ") AS exists",
                {"table_name": table_name},
            )
        ).first()
    return bool(row.exists) if row else False


async def _table_columns(table_name: str) -> set[str]:
    async with db_session_context() as session:
        rows = (
            await execute_raw_sql(
                session,
                "SELECT column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table_name",
                {"table_name": table_name},
            )
        ).all()
    return {str(row.column_name) for row in rows}


def _pick_column(columns: set[str], preferred: list[str], fallback: str) -> str:
    for name in preferred:
        if name in columns:
            return name
    return fallback


@router.get("/reddit")
async def get_live_reddit_feed(
    coin: str | None = Query(default=None, description="Coin filter, e.g. BTC, ETH, DOGE"),
    minutes: int = Query(default=360, ge=5, le=7 * 24 * 60),
    limit: int = Query(default=40, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Return recent Reddit posts/comments used in live model context."""

    reddit_table = None
    for candidate in ("reddit_data", "reddit_posts"):
        if await _table_exists(candidate):
            reddit_table = candidate
            break

    if reddit_table is None:
        raise HTTPException(status_code=404, detail="No Reddit table found")

    columns = await _table_columns(reddit_table)

    id_col = _pick_column(columns, ["post_id", "reddit_id", "id"], "id")
    ts_col = _pick_column(columns, ["created_utc", "ts", "created_at"], "created_at")
    title_col = _pick_column(columns, ["title"], "NULL")
    body_col = _pick_column(columns, ["body", "selftext"], "NULL")
    subreddit_col = _pick_column(columns, ["subreddit"], "NULL")
    score_col = _pick_column(columns, ["score"], "NULL")
    comments_col = _pick_column(columns, ["num_comments"], "NULL")
    upvote_col = _pick_column(columns, ["upvote_ratio"], "NULL")
    url_col = _pick_column(columns, ["url", "permalink"], "NULL")
    source_type_col = _pick_column(columns, ["source_type"], "'post'")
    mentions_col = _pick_column(columns, ["mentioned_coins"], "ARRAY[]::text[]")

    where_clauses = [f"{ts_col} >= :cutoff"]
    params: dict[str, Any] = {
        "cutoff": datetime.now(UTC) - timedelta(minutes=minutes),
        "limit": limit,
    }

    if coin:
        coin_upper = coin.upper()
        params["coin"] = coin_upper
        params["coin_like"] = f"%{coin_upper}%"

        if mentions_col in columns:
            where_clauses.append(":coin = ANY(COALESCE(mentioned_coins, ARRAY[]::text[]))")
        else:
            title_filter = f"{title_col} ILIKE :coin_like" if title_col in columns else "FALSE"
            body_filter = f"{body_col} ILIKE :coin_like" if body_col in columns else "FALSE"
            where_clauses.append(f"({title_filter} OR {body_filter})")

    sql = (
        "SELECT "
        f"{id_col}::text AS item_id, "
        f"{ts_col} AS ts, "
        f"{source_type_col}::text AS source_type, "
        f"{subreddit_col}::text AS subreddit, "
        f"{title_col}::text AS title, "
        f"{body_col}::text AS body, "
        f"COALESCE({score_col}, 0)::int AS score, "
        f"COALESCE({comments_col}, 0)::int AS num_comments, "
        f"COALESCE({upvote_col}, 0)::double precision AS upvote_ratio, "
        f"{url_col}::text AS url, "
        f"COALESCE({mentions_col}, ARRAY[]::text[]) AS mentioned_coins "
        f"FROM {reddit_table} "
        f"WHERE {' AND '.join(where_clauses)} "
        f"ORDER BY {ts_col} DESC "
        "LIMIT :limit"
    )

    async with db_session_context() as session:
        rows = (await execute_raw_sql(session, sql, params)).all()

    payload: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row._mapping)
        mentions = item.get("mentioned_coins")
        if isinstance(mentions, str):
            mentions = [mentions]
        if mentions is None:
            mentions = []

        payload.append(
            {
                "item_id": item.get("item_id"),
                "ts": item.get("ts"),
                "source": "reddit",
                "source_type": item.get("source_type") or "post",
                "subreddit": item.get("subreddit"),
                "title": item.get("title"),
                "body": item.get("body"),
                "score": int(item.get("score") or 0),
                "num_comments": int(item.get("num_comments") or 0),
                "upvote_ratio": float(item.get("upvote_ratio") or 0.0),
                "url": item.get("url"),
                "mentioned_coins": mentions,
            }
        )

    return payload
