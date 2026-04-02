#!/usr/bin/env python3
"""
Comprehensive news collection and market data linking pipeline.
Pulls maximum news for BTC, ETH, and DOGE within token budget and links with price data.
"""

import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any

# Ensure repo root is importable when running this file directly via:
# python .\\scripts\\pull_news_and_link_market_data.py
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.collectors.news_collector import NewsCollector
from backend.database import db_manager
from sqlalchemy import text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def validate_schema() -> None:
    """Ensure required tables exist for the pipeline."""
    logger.info("Validating database schema...")
    
    async with db_manager.session_factory() as session:
        try:
            # Check if news_articles table exists
            result = await session.execute(
                text("""
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = 'news_articles'
                """)
            )
            if not result.scalar():
                raise RuntimeError("news_articles table not found. Run database/schema.sql")

            result = await session.execute(
                text("""
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_name = 'news_market_linkage'
                """)
            )
            if not result.scalar():
                raise RuntimeError("news_market_linkage table not found. Run database/schema.sql")
            
            logger.info("✓ Schema validation passed")
        except Exception as exc:
            logger.error(f"Schema validation failed: {exc}")
            raise


async def collect_maximum_news() -> int:
    """
    Collect maximum news articles for BTC, ETH, and DOGE using all available tokens.
    With 2000 token budget and 1 token per search, we can do 2000 searches.
    Expected result: 100,000+ articles
    """
    logger.info("Starting maximum news collection pipeline...")
    logger.info(f"Token budget: 2000 (estimated 100,000+ articles)")
    
    total_articles = 0
    
    async with NewsCollector() as collector:
        try:
            # Phase 1: NewsAPI collection
            logger.info("=" * 60)
            logger.info("PHASE 1: NewsAPI Collection (Optimized Queries)")
            logger.info("=" * 60)
            
            articles = await collector.collect_from_newsapi()
            total_articles += len(articles)
            logger.info(f"✓ Collected {len(articles)} articles from NewsAPI")
            
            # Save to database
            await collector.save_to_db(articles)
            logger.info(f"✓ Saved {len(articles)} articles to database")
            
            # Phase 2: RSS collection (supplementary)
            logger.info("\n" + "=" * 60)
            logger.info("PHASE 2: RSS Feed Collection (Supplementary)")
            logger.info("=" * 60)
            
            rss_articles = await collector.collect_from_rss()
            total_articles += len(rss_articles)
            logger.info(f"✓ Collected {len(rss_articles)} articles from RSS feeds")
            
            await collector.save_to_db(rss_articles)
            logger.info(f"✓ Saved {len(rss_articles)} RSS articles to database")
            
        except Exception as exc:
            logger.error(f"Error during collection: {exc}")
            raise
    
    return total_articles


async def link_news_with_market_data() -> int:
    """
    Link collected news articles with price data.
    Creates entries in news_market_linkage table for analysis.
    """
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 3: Link News with Market Data")
    logger.info("=" * 60)
    
    linked_count = 0
    
    async with db_manager.session_factory() as session:
        try:
            # Get all news articles that mention BTC, ETH, or DOGE
            logger.info("Fetching unlinked news articles...")
            
            result = await session.execute(text("""
                SELECT 
                    id, ts, title, mentioned_coins, primary_symbol
                FROM news_articles
                WHERE (mentioned_coins @> ARRAY['BTC']::text[] 
                   OR mentioned_coins @> ARRAY['ETH']::text[]
                   OR mentioned_coins @> ARRAY['DOGE']::text[]
                   OR primary_symbol IS NOT NULL)
                AND NOT EXISTS (
                    SELECT 1 FROM news_market_linkage 
                    WHERE news_market_linkage.news_id = news_articles.id
                )
                ORDER BY ts DESC
                LIMIT 10000
            """))
            
            articles = result.fetchall()
            logger.info(f"Found {len(articles)} articles to link")
            
            if not articles:
                logger.info("No articles to link")
                return 0
            
            # For each article, create linkage entries
            for article_id, article_ts, title, mentioned_coins, primary_symbol in articles:
                # Determine symbols to link
                symbols_to_link = set()
                
                if mentioned_coins:
                    symbols_to_link.update(mentioned_coins)
                if primary_symbol:
                    symbols_to_link.add(primary_symbol)
                if not symbols_to_link:
                    # Default to BTC, ETH, and DOGE if not specified
                    symbols_to_link = {"BTC", "ETH", "DOGE"}

                # Only link BTC, ETH, and DOGE
                symbols_to_link = symbols_to_link & {"BTC", "ETH", "DOGE"}
                
                if not symbols_to_link:
                    continue
                
                for symbol in symbols_to_link:
                    try:
                        # Insert linkage entry
                        await session.execute(text("""
                            INSERT INTO news_market_linkage (ts, news_id, symbol, time_window, metadata)
                            VALUES (:ts, :news_id, :symbol, '1h', CAST(:metadata AS JSONB))
                            ON CONFLICT DO NOTHING
                        """), {
                            "ts": article_ts,
                            "news_id": article_id,
                            "symbol": symbol,
                            "metadata": json.dumps({"article_title": (title or "")[:100]})
                        })
                        linked_count += 1
                    except Exception as exc:
                        logger.warning(f"Failed to link article {article_id}: {exc}")
                        continue
            
            await session.commit()
            logger.info(f"✓ Created {linked_count} market linkage entries")
            
        except Exception as exc:
            await session.rollback()
            logger.error(f"Error during linking: {exc}")
            raise
    
    return linked_count


async def calculate_price_correlation() -> None:
    """
    Calculate correlation between news timing and price movements.
    Updates news_market_linkage with lagged price changes.
    """
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 4: Calculate Price Correlations")
    logger.info("=" * 60)
    
    async with db_manager.session_factory() as session:
        try:
            # Get news-price pairs within 1 hour window
            logger.info("Calculating price changes after news publication...")
            
            update_count = await session.execute(text("""
                UPDATE news_market_linkage nml
                SET 
                    lagged_price_change_pct = (
                        SELECT (pd2.close - pd1.close) / pd1.close * 100
                        FROM price_data pd1
                        JOIN price_data pd2 ON pd1.symbol = pd2.symbol AND pd1.interval = '1h'
                        WHERE pd2.interval = '1h'
                            AND pd1.symbol = nml.symbol || 'USDT'
                            AND pd1.ts <= nml.ts
                            AND pd2.ts > nml.ts
                            AND pd2.ts <= nml.ts + INTERVAL '1 hour'
                        ORDER BY pd2.ts ASC
                        LIMIT 1
                    ),
                    metadata = jsonb_set(
                        nml.metadata,
                        '{correlation_updated_at}',
                        to_jsonb(now())
                    )
                WHERE nml.lagged_price_change_pct IS NULL
                AND EXISTS (
                    SELECT 1 FROM price_data pd 
                    WHERE pd.symbol = nml.symbol || 'USDT'
                    AND pd.ts >= nml.ts - INTERVAL '1 hour'
                    AND pd.ts <= nml.ts + INTERVAL '2 hours'
                )
                RETURNING nml.id
            """))
            
            # Count updated rows by executing the query
            updated_rows = update_count.fetchall()
            logger.info(f"✓ Updated price correlations for {len(updated_rows)} linkages")
            await session.commit()
            
        except Exception as exc:
            await session.rollback()
            logger.error(f"Error calculating correlations: {exc}")
            logger.info("Continuing anyway - correlations can be calculated later")


async def generate_summary() -> None:
    """Generate summary statistics of collected data."""
    logger.info("\n" + "=" * 60)
    logger.info("DATA COLLECTION SUMMARY")
    logger.info("=" * 60)
    
    async with db_manager.session_factory() as session:
        try:
            # News articles count
            result = await session.execute(text("SELECT COUNT(*) FROM news_articles"))
            news_count = result.scalar() or 0
            logger.info(f"Total news articles collected: {news_count:,}")
            
            # By symbol
            result = await session.execute(text("""
                SELECT symbol, COUNT(*) as count
                FROM news_market_linkage
                GROUP BY symbol
                ORDER BY count DESC
            """))
            for symbol, count in result.fetchall():
                logger.info(f"  - {symbol}: {count:,} linkages")
            
            # Date range
            result = await session.execute(text("""
                SELECT 
                    MIN(ts) as earliest,
                    MAX(ts) as latest
                FROM news_articles
            """))
            earliest, latest = result.fetchone()
            logger.info(f"\nData date range:")
            logger.info(f"  - Earliest: {earliest}")
            logger.info(f"  - Latest: {latest}")
            
            # By source
            result = await session.execute(text("""
                SELECT source_name, COUNT(*) as count
                FROM news_articles
                GROUP BY source_name
                ORDER BY count DESC
                LIMIT 5
            """))
            logger.info(f"\nTop news sources:")
            for source, count in result.fetchall():
                logger.info(f"  - {source}: {count:,}")
            
            # Sentiment analysis ready
            result = await session.execute(text("""
                SELECT COUNT(*) FROM news_market_linkage 
                WHERE lagged_price_change_pct IS NOT NULL
            """))
            correlated_count = result.scalar() or 0
            logger.info(f"\nNews with price correlation: {correlated_count:,}")
            
        except Exception as exc:
            logger.warning(f"Error generating summary: {exc}")


async def main() -> None:
    """Main execution pipeline."""
    logger.info("╔════════════════════════════════════════════════════════════╗")
    logger.info("║   BinFin News Collection & Market Data Linking Pipeline   ║")
    logger.info("╚════════════════════════════════════════════════════════════╝")
    
    try:
        # Validate schema
        await validate_schema()
        
        # Collect maximum news
        total_collected = await collect_maximum_news()
        logger.info(f"\n✓ Total articles collected: {total_collected:,}")
        
        # Link with market data
        linked_count = await link_news_with_market_data()
        
        # Calculate correlations
        await calculate_price_correlation()
        
        # Generate summary
        await generate_summary()
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ Pipeline completed successfully!")
        logger.info("=" * 60)
        logger.info("\nNext steps:")
        logger.info("1. Query linked news-price data for analysis")
        logger.info("2. Train sentiment analysis model on news")
        logger.info("3. Use correlations for feature engineering")
        logger.info("4. Monitor real-time news for trading signals")
        
    except Exception as exc:
        logger.error(f"\n✗ Pipeline failed: {exc}")
        raise
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
