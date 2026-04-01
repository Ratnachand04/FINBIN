from __future__ import annotations

import asyncio
import importlib
import logging
import os
import re
import signal
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable

from prometheus_client import Counter, Gauge
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.database import bulk_insert, db_manager, upsert

logger = logging.getLogger(__name__)

REDDIT_POSTS_COLLECTED = Counter(
    "binfin_reddit_posts_collected_total",
    "Total number of Reddit posts/comments collected",
)
REDDIT_POSTS_PER_MIN = Gauge(
    "binfin_reddit_posts_per_min",
    "Rolling posts collected per minute",
)
REDDIT_COLLECTOR_UP = Gauge(
    "binfin_reddit_collector_up",
    "Reddit collector health status (1=up, 0=down)",
)


class RedditCollector:
    """Async Reddit collector using PRAW with threaded execution for API calls."""

    DEFAULT_SUBREDDITS = ["cryptocurrency", "bitcoin", "ethereum", "CryptoMarkets"]
    COIN_PATTERNS: dict[str, str] = {
        "BTC": r"\b(?:\$?BTC|Bitcoin)\b",
        "ETH": r"\b(?:\$?ETH|Ethereum)\b",
        "SOL": r"\b(?:\$?SOL|Solana)\b",
        "ADA": r"\b(?:\$?ADA|Cardano)\b",
        "DOT": r"\b(?:\$?DOT|Polkadot)\b",
        "BNB": r"\b(?:\$?BNB|Binance\s*Coin)\b",
        "XRP": r"\b(?:\$?XRP|Ripple)\b",
        "DOGE": r"\b(?:\$?DOGE|Dogecoin)\b",
        "MATIC": r"\b(?:\$?MATIC|Polygon)\b",
        "AVAX": r"\b(?:\$?AVAX|Avalanche)\b",
        "LINK": r"\b(?:\$?LINK|Chainlink)\b",
    }

    def __init__(self) -> None:
        self._praw = self._load_praw()
        self.client_id = os.getenv("REDDIT_CLIENT_ID", "")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        self.user_agent = os.getenv("REDDIT_USER_AGENT", "binfin/1.0")
        self.min_score = int(os.getenv("REDDIT_MIN_SCORE", "5"))
        self.min_comment_upvote_ratio = float(os.getenv("REDDIT_MIN_COMMENT_UPVOTE_RATIO", "0.5"))
        self.max_requests_per_minute = 60
        self._request_timestamps: deque[float] = deque(maxlen=240)
        self._posts_window: deque[float] = deque(maxlen=2000)
        self._shutdown = asyncio.Event()
        self._reddit_client: Any | None = None

        self._coin_regex: dict[str, re.Pattern[str]] = {
            symbol: re.compile(pattern, flags=re.IGNORECASE)
            for symbol, pattern in self.COIN_PATTERNS.items()
        }

    def _load_praw(self) -> Any:
        try:
            return importlib.import_module("praw")
        except Exception as exc:  # pragma: no cover - dependency bootstrap
            raise RuntimeError("praw is required for RedditCollector") from exc

    async def __aenter__(self) -> "RedditCollector":
        self._init_client()
        self._register_signal_handlers()
        REDDIT_COLLECTOR_UP.set(1)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.shutdown()

    def _init_client(self) -> None:
        if not self.client_id or not self.client_secret or not self.user_agent:
            raise RuntimeError("Reddit credentials are not configured in environment variables")

        self._reddit_client = self._praw.Reddit(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
        )

    def _register_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown.set)
            except NotImplementedError:
                # Windows event loop can skip this.
                pass

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        while self._request_timestamps and (now - self._request_timestamps[0] > 60):
            self._request_timestamps.popleft()

        if len(self._request_timestamps) >= self.max_requests_per_minute:
            wait_time = 60 - (now - self._request_timestamps[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        self._request_timestamps.append(time.monotonic())

    async def _with_retry(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        retries = 5
        for attempt in range(retries):
            try:
                await self._rate_limit()
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as exc:
                backoff = min(2 ** attempt, 30)
                logger.warning(
                    "Reddit API call failed (attempt %s/%s): %s. Retrying in %ss",
                    attempt + 1,
                    retries,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
        raise RuntimeError("Reddit API call failed after retries")

    def extract_coin_mentions(self, text: str) -> list[str]:
        if not text:
            return []

        found: set[str] = set()
        for symbol, pattern in self._coin_regex.items():
            if pattern.search(text):
                found.add(symbol)
        return sorted(found)

    def _format_post(self, post: Any) -> dict[str, Any]:
        created = datetime.fromtimestamp(float(post.created_utc), tz=timezone.utc)
        combined_text = f"{post.title or ''}\n{post.selftext or ''}"
        return {
            "post_id": str(post.id),
            "subreddit": str(post.subreddit.display_name),
            "title": post.title,
            "body": post.selftext,
            "author": str(post.author) if post.author else None,
            "score": int(post.score or 0),
            "num_comments": int(post.num_comments or 0),
            "upvote_ratio": float(post.upvote_ratio or 0),
            "created_utc": created,
            "url": getattr(post, "url", None),
            "mentioned_coins": self.extract_coin_mentions(combined_text),
            "collected_at": datetime.now(timezone.utc),
        }

    def _format_comment(self, comment: Any, subreddit: str) -> dict[str, Any]:
        created = datetime.fromtimestamp(float(comment.created_utc), tz=timezone.utc)
        body = getattr(comment, "body", "") or ""
        return {
            "post_id": f"comment_{comment.id}",
            "subreddit": subreddit,
            "title": None,
            "body": body,
            "author": str(comment.author) if comment.author else None,
            "score": int(comment.score or 0),
            "num_comments": 0,
            "upvote_ratio": float(getattr(comment, "upvote_ratio", 1.0) or 1.0),
            "created_utc": created,
            "url": None,
            "mentioned_coins": self.extract_coin_mentions(body),
            "collected_at": datetime.now(timezone.utc),
        }

    async def stream_posts(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._reddit_client is None:
            self._init_client()

        subreddits = "+".join(self.DEFAULT_SUBREDDITS)
        subreddit = await self._with_retry(self._reddit_client.subreddit, subreddits)
        stream = subreddit.stream.submissions(skip_existing=True)

        while not self._shutdown.is_set():
            try:
                post = await asyncio.to_thread(next, stream)
                if post is None:
                    await asyncio.sleep(0.2)
                    continue

                structured = self._format_post(post)
                if structured["score"] < self.min_score:
                    continue

                self._track_post_metric()
                yield structured
            except StopIteration:
                await asyncio.sleep(0.2)
            except Exception as exc:
                logger.exception("stream_posts error: %s", exc)
                await asyncio.sleep(2)

    async def collect_hot_posts(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._reddit_client is None:
            self._init_client()

        collected: dict[str, dict[str, Any]] = {}
        per_subreddit_limit = max(1, limit // len(self.DEFAULT_SUBREDDITS))

        for subreddit_name in self.DEFAULT_SUBREDDITS:
            subreddit = await self._with_retry(self._reddit_client.subreddit, subreddit_name)
            hot_posts = await self._with_retry(lambda: list(subreddit.hot(limit=per_subreddit_limit)))

            for post in hot_posts:
                structured = self._format_post(post)
                if structured["score"] < self.min_score:
                    continue
                collected[structured["post_id"]] = structured

        posts = list(collected.values())[:limit]
        if posts:
            REDDIT_POSTS_COLLECTED.inc(len(posts))
            now = time.time()
            for _ in posts:
                self._posts_window.append(now)
            self._refresh_posts_per_min_metric()
        return posts

    async def collect_comments(self, post_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if self._reddit_client is None:
            self._init_client()

        submission = await self._with_retry(self._reddit_client.submission, id=post_id)
        await self._with_retry(submission.comments.replace_more, limit=0)

        comments = await self._with_retry(lambda: list(submission.comments.list()))
        output: list[dict[str, Any]] = []

        for comment in comments[:limit]:
            row = self._format_comment(comment, str(submission.subreddit.display_name))
            if row["upvote_ratio"] < self.min_comment_upvote_ratio:
                continue
            output.append(row)

        if output:
            REDDIT_POSTS_COLLECTED.inc(len(output))
            now = time.time()
            for _ in output:
                self._posts_window.append(now)
            self._refresh_posts_per_min_metric()
        return output

    async def save_to_db(self, posts: list[dict[str, Any]]) -> None:
        if not posts:
            return

        async with db_manager.session_factory() as session:
            try:
                try:
                    await bulk_insert(session, "reddit_data", posts)
                except IntegrityError:
                    # If duplicates exist, fallback to idempotent upsert.
                    for post in posts:
                        await upsert(
                            session=session,
                            table_name="reddit_data",
                            values=post,
                            conflict_columns=["post_id"],
                            update_columns=[
                                "score",
                                "num_comments",
                                "upvote_ratio",
                                "mentioned_coins",
                                "collected_at",
                            ],
                        )

                await session.commit()
            except SQLAlchemyError as exc:
                await session.rollback()
                logger.exception("Failed to persist Reddit posts: %s", exc)
                raise

        try:
            cache_payload = {"count": len(posts), "updated_at": datetime.now(timezone.utc).isoformat()}
            await db_manager.redis_client.set("reddit:last_sync", str(cache_payload), ex=300)
        except Exception as exc:
            logger.warning("Redis cache update failed for reddit collector: %s", exc)

    async def run(self) -> None:
        logger.info("Starting Reddit collector main loop")
        REDDIT_COLLECTOR_UP.set(1)

        while not self._shutdown.is_set():
            try:
                posts = await self.collect_hot_posts(limit=100)
                await self.save_to_db(posts)
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                logger.info("Reddit collector cancelled")
                break
            except Exception as exc:
                logger.exception("Reddit collector loop error: %s", exc)
                await asyncio.sleep(5)

        await self.shutdown()

    async def shutdown(self) -> None:
        self._shutdown.set()
        REDDIT_COLLECTOR_UP.set(0)
        logger.info("Reddit collector shutdown complete")

    async def health_check(self) -> dict[str, Any]:
        db_ok = await db_manager.check_db_health()
        redis_ok = await db_manager.check_redis_health()
        return {
            "status": "ok" if db_ok and redis_ok and not self._shutdown.is_set() else "degraded",
            "db_ok": db_ok,
            "redis_ok": redis_ok,
            "collector_running": not self._shutdown.is_set(),
            "posts_per_min": self._current_posts_per_min(),
        }

    def _track_post_metric(self) -> None:
        REDDIT_POSTS_COLLECTED.inc()
        self._posts_window.append(time.time())
        self._refresh_posts_per_min_metric()

    def _refresh_posts_per_min_metric(self) -> None:
        REDDIT_POSTS_PER_MIN.set(self._current_posts_per_min())

    def _current_posts_per_min(self) -> float:
        cutoff = time.time() - 60
        while self._posts_window and self._posts_window[0] < cutoff:
            self._posts_window.popleft()
        return float(len(self._posts_window))
