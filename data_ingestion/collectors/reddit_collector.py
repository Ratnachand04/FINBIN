from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, AsyncGenerator

from config.settings import get_settings

logger = logging.getLogger(__name__)

COIN_PATTERNS = {
    "BTC": re.compile(r"\b(?:BTC|Bitcoin|\$BTC)\b", re.IGNORECASE),
    "ETH": re.compile(r"\b(?:ETH|Ethereum|\$ETH)\b", re.IGNORECASE),
    "SOL": re.compile(r"\b(?:SOL|Solana|\$SOL)\b", re.IGNORECASE),
    "ADA": re.compile(r"\b(?:ADA|Cardano|\$ADA)\b", re.IGNORECASE),
    "DOT": re.compile(r"\b(?:DOT|Polkadot|\$DOT)\b", re.IGNORECASE),
}


@dataclass
class RedditRecord:
    post_id: str
    subreddit: str
    title: str | None
    body: str | None
    score: int
    created_utc: float
    mentioned_coins: list[str]


class TokenBucket:
    def __init__(self, rate_per_minute: int) -> None:
        self.capacity = max(rate_per_minute, 1)
        self.tokens = float(self.capacity)
        self.refill_per_second = float(self.capacity) / 60.0
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
                self.last_refill = now
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return
                await asyncio.sleep(0.2)


class RedditCollector:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.subreddits = ["cryptocurrency", "bitcoin", "ethereum"]
        self.bucket = TokenBucket(self.settings.reddit_rate_limit_per_minute)
        self._seen_ids: deque[str] = deque(maxlen=5000)
        self._seen_set: set[str] = set()

    def extract_coin_mentions(self, text: str) -> list[str]:
        found = [symbol for symbol, pattern in COIN_PATTERNS.items() if pattern.search(text or "")]
        return sorted(set(found))

    async def stream_posts(self) -> AsyncGenerator[RedditRecord, None]:
        try:
            import asyncpraw  # type: ignore
        except Exception as exc:
            logger.warning("asyncpraw unavailable, reddit stream disabled: %s", exc)
            return

        reddit = asyncpraw.Reddit(
            client_id=self.settings.reddit_client_id,
            client_secret=self.settings.reddit_client_secret,
            user_agent=self.settings.reddit_user_agent,
        )
        multireddit = await reddit.subreddit("+".join(self.subreddits))

        backoff = 1
        while True:
            try:
                async for submission in multireddit.stream.submissions(skip_existing=True):
                    await self.bucket.acquire()
                    post_id = str(submission.id)
                    if post_id in self._seen_set:
                        continue
                    self._track_seen(post_id)

                    text = f"{submission.title or ''}\n{submission.selftext or ''}"
                    yield RedditRecord(
                        post_id=post_id,
                        subreddit=str(submission.subreddit.display_name),
                        title=submission.title,
                        body=submission.selftext,
                        score=int(submission.score or 0),
                        created_utc=float(submission.created_utc),
                        mentioned_coins=self.extract_coin_mentions(text),
                    )
                backoff = 1
            except Exception as exc:
                logger.exception("reddit stream failure, retrying: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def collect_batch(self, batch_size: int = 100) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        async for post in self.stream_posts():
            records.append(post.__dict__)
            if len(records) >= batch_size:
                return records
        return records

    def _track_seen(self, post_id: str) -> None:
        self._seen_ids.append(post_id)
        self._seen_set.add(post_id)
        while len(self._seen_set) > self._seen_ids.maxlen:
            stale = self._seen_ids.popleft()
            self._seen_set.discard(stale)
