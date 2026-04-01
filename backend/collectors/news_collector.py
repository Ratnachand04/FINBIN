from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import os
import time
from collections import deque
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from prometheus_client import Counter, Gauge
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.database import bulk_insert, db_manager, upsert

logger = logging.getLogger(__name__)

NEWS_ARTICLES_COLLECTED = Counter("binfin_news_articles_collected_total", "Total collected news articles")
NEWS_COLLECTOR_UP = Gauge("binfin_news_collector_up", "News collector health status (1=up, 0=down)")
NEWS_REQUESTS_DAILY = Gauge("binfin_newsapi_requests_daily", "NewsAPI requests made today")


class NewsCollector:
    NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
    NEWS_KEYWORDS = "bitcoin OR ethereum OR crypto OR blockchain"
    NEWS_SOURCES = "coindesk,cointelegraph,decrypt"
    RSS_FEEDS = [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://theblock.co/rss.xml",
    ]
    EXCHANGES = {"BINANCE", "COINBASE", "KRAKEN", "OKX", "BYBIT"}
    PEOPLE = {"VITALIK", "SATOSHI", "CZ", "SAM BANKMAN-FRIED"}
    COINS = {
        "BTC": ["BTC", "$BTC", "BITCOIN"],
        "ETH": ["ETH", "$ETH", "ETHEREUM"],
        "SOL": ["SOL", "$SOL", "SOLANA"],
        "ADA": ["ADA", "$ADA", "CARDANO"],
        "DOT": ["DOT", "$DOT", "POLKADOT"],
    }

    def __init__(self) -> None:
        self.news_api_key = os.getenv("NEWS_API_KEY", "")
        self.rate_limit_per_minute = 30
        self._request_timestamps: deque[float] = deque(maxlen=200)
        self._shutdown = asyncio.Event()
        self._feedparser = self._load_optional("feedparser")
        self._newspaper = self._load_optional("newspaper")

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency not available: %s", module_name)
            return None

    async def __aenter__(self) -> "NewsCollector":
        NEWS_COLLECTOR_UP.set(1)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.shutdown()

    async def _rate_limit(self) -> None:
        now = time.monotonic()
        while self._request_timestamps and now - self._request_timestamps[0] > 60:
            self._request_timestamps.popleft()
        if len(self._request_timestamps) >= self.rate_limit_per_minute:
            wait_s = 60 - (now - self._request_timestamps[0])
            if wait_s > 0:
                await asyncio.sleep(wait_s)
        self._request_timestamps.append(time.monotonic())

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        for attempt in range(5):
            try:
                await self._rate_limit()
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:
                delay = min(2**attempt, 20)
                logger.warning("HTTP request failed for %s (attempt %s): %s", url, attempt + 1, exc)
                await asyncio.sleep(delay)
        raise RuntimeError(f"Request failed after retries: {url}")

    async def _can_use_newsapi(self) -> bool:
        day_key = datetime.now(UTC).strftime("%Y%m%d")
        key = f"newsapi:requests:{day_key}"
        try:
            current = await db_manager.redis_client.get(key)
            current_int = int(current) if current else 0
            NEWS_REQUESTS_DAILY.set(current_int)
            return current_int < 100
        except Exception:
            return True

    async def _increment_newsapi_counter(self) -> None:
        day_key = datetime.now(UTC).strftime("%Y%m%d")
        key = f"newsapi:requests:{day_key}"
        try:
            count = await db_manager.redis_client.incr(key)
            await db_manager.redis_client.expire(key, 86400)
            NEWS_REQUESTS_DAILY.set(float(count))
        except Exception as exc:
            logger.warning("Unable to update NewsAPI counter cache: %s", exc)

    async def collect_from_newsapi(self) -> list[dict[str, Any]]:
        if not self.news_api_key:
            logger.warning("NEWS_API_KEY is missing; skipping NewsAPI collection")
            return []
        if not await self._can_use_newsapi():
            logger.info("NewsAPI daily quota reached (100/day). Skipping requests.")
            return []

        params = {
            "q": self.NEWS_KEYWORDS,
            "sources": self.NEWS_SOURCES,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": 100,
            "apiKey": self.news_api_key,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await self._request_with_retry(client, "GET", self.NEWSAPI_ENDPOINT, params=params)

        await self._increment_newsapi_counter()
        payload = response.json()
        rows: list[dict[str, Any]] = []
        for article in payload.get("articles", []):
            url = article.get("url")
            title = article.get("title")
            if not url or not title:
                continue
            content = article.get("content") or article.get("description") or ""
            published_at = self._parse_datetime(article.get("publishedAt"))
            row = self._normalize_article(
                title=title,
                content=content,
                url=url,
                source=article.get("source", {}).get("name"),
                author=article.get("author"),
                published_at=published_at,
            )
            rows.append(row)
        return rows

    async def collect_from_rss(self) -> list[dict[str, Any]]:
        articles: list[dict[str, Any]] = []
        if not self._feedparser:
            logger.warning("feedparser not installed; skipping RSS collection")
            return articles

        for feed_url in self.RSS_FEEDS:
            try:
                parsed = await asyncio.to_thread(self._feedparser.parse, feed_url)
                for entry in parsed.entries:
                    title = getattr(entry, "title", None)
                    link = getattr(entry, "link", None)
                    if not title or not link:
                        continue

                    summary = getattr(entry, "summary", "") or ""
                    published = getattr(entry, "published", None) or getattr(entry, "updated", None)
                    row = self._normalize_article(
                        title=title,
                        content=summary,
                        url=link,
                        source=urlparse(feed_url).netloc,
                        author=getattr(entry, "author", None),
                        published_at=self._parse_datetime(published),
                    )
                    articles.append(row)
            except Exception as exc:
                logger.warning("RSS parsing failed for %s: %s", feed_url, exc)

        return self.deduplicate_articles(articles)

    async def scrape_website(self, url: str, max_pages: int = 3) -> list[dict[str, Any]]:
        if not await self._is_allowed_by_robots(url):
            logger.info("Skipping %s due to robots.txt", url)
            return []
        if not self._newspaper:
            logger.warning("newspaper3k not installed; skipping scrape for %s", url)
            return []

        visited: set[str] = set()
        to_visit = [url]
        output: list[dict[str, Any]] = []

        while to_visit and len(visited) < max_pages:
            current = to_visit.pop(0)
            if current in visited:
                continue
            visited.add(current)
            try:
                article_obj = self._newspaper.Article(current)
                await asyncio.to_thread(article_obj.download)
                await asyncio.to_thread(article_obj.parse)

                title = article_obj.title or ""
                text = article_obj.text or ""
                if title and text:
                    output.append(
                        self._normalize_article(
                            title=title,
                            content=text,
                            url=current,
                            source=urlparse(current).netloc,
                            author=", ".join(article_obj.authors) if article_obj.authors else None,
                            published_at=article_obj.publish_date,
                        )
                    )

                next_url = self._next_page_url(current)
                if next_url and next_url not in visited:
                    to_visit.append(next_url)
            except Exception as exc:
                logger.warning("Failed to scrape %s: %s", current, exc)

        return self.deduplicate_articles(output)

    def extract_entities(self, text: str) -> dict[str, Any]:
        normalized = (text or "").upper()
        coins = [symbol for symbol, aliases in self.COINS.items() if any(alias in normalized for alias in aliases)]
        exchanges = [ex for ex in self.EXCHANGES if ex in normalized]
        people = [person for person in self.PEOPLE if person in normalized]
        return {
            "coins": sorted(set(coins)),
            "exchanges": sorted(set(exchanges)),
            "people": sorted(set(people)),
        }

    def deduplicate_articles(self, articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()

        for article in articles:
            content_hash = article["url_hash"]
            if content_hash in seen_hashes:
                continue

            is_near_duplicate = False
            for existing in unique:
                title_sim = self._similarity(article.get("title", ""), existing.get("title", ""))
                content_sim = self._minhash_similarity(article.get("content", ""), existing.get("content", ""))
                if title_sim >= 0.80 and content_sim >= 0.90:
                    is_near_duplicate = True
                    break

            if not is_near_duplicate:
                unique.append(article)
                seen_hashes.add(content_hash)

        return unique

    async def save_to_db(self, articles: list[dict[str, Any]]) -> None:
        if not articles:
            return

        deduped = self.deduplicate_articles(articles)
        async with db_manager.session_factory() as session:
            try:
                try:
                    await bulk_insert(session, "news_data", deduped)
                except IntegrityError:
                    for article in deduped:
                        await upsert(
                            session=session,
                            table_name="news_data",
                            values=article,
                            conflict_columns=["url_hash"],
                            update_columns=[
                                "title",
                                "content",
                                "mentioned_coins",
                                "extracted_entities",
                                "sentiment_score",
                                "metadata",
                            ],
                        )

                await session.commit()
                NEWS_ARTICLES_COLLECTED.inc(len(deduped))
            except SQLAlchemyError as exc:
                await session.rollback()
                logger.exception("Failed to save news articles: %s", exc)
                raise

        try:
            await db_manager.redis_client.set(
                "news:last_sync",
                datetime.now(UTC).isoformat(),
                ex=300,
            )
        except Exception as exc:
            logger.warning("Unable to update news cache key: %s", exc)

    async def shutdown(self) -> None:
        self._shutdown.set()
        NEWS_COLLECTOR_UP.set(0)

    def _normalize_article(
        self,
        title: str,
        content: str,
        url: str,
        source: str | None,
        author: str | None,
        published_at: datetime | None,
    ) -> dict[str, Any]:
        clean_content = content or ""
        combined_text = f"{title}\n{clean_content}"
        entities = self.extract_entities(combined_text)
        url_hash = hashlib.sha256(url.strip().encode("utf-8")).hexdigest()
        return {
            "url_hash": url_hash,
            "source_name": source,
            "author": author,
            "title": title,
            "description": clean_content[:400] if clean_content else None,
            "content": clean_content,
            "url": url,
            "published_at": published_at,
            "ts": published_at or datetime.now(UTC),
            "mentioned_coins": entities["coins"],
            "extracted_entities": entities,
            "sentiment_score": None,
            "metadata": {"collected_at": datetime.now(UTC).isoformat()},
            "created_at": datetime.now(UTC),
        }

    async def _is_allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        def _check() -> bool:
            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch("BINFIN-NewsCollector", url)

        try:
            return await asyncio.to_thread(_check)
        except Exception:
            return False

    def _next_page_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        page = int(query.get("page", ["1"])[0])
        if page >= 3:
            return None
        query["page"] = [str(page + 1)]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

    def _parse_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                if value.endswith("Z"):
                    value = value[:-1] + "+00:00"
                parsed = datetime.fromisoformat(value)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except Exception:
                return None
        return None

    def _similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()

    def _minhash_signature(self, text: str, num_hashes: int = 64) -> set[int]:
        tokens = (text or "").lower().split()
        shingles = {" ".join(tokens[i : i + 3]) for i in range(max(1, len(tokens) - 2))}
        signature: set[int] = set()
        for idx in range(num_hashes):
            min_hash = min((hash(f"{idx}:{s}") for s in shingles), default=0)
            signature.add(min_hash)
        return signature

    def _minhash_similarity(self, left: str, right: str) -> float:
        l_sig = self._minhash_signature(left)
        r_sig = self._minhash_signature(right)
        union = len(l_sig | r_sig)
        if union == 0:
            return 0.0
        return len(l_sig & r_sig) / union
