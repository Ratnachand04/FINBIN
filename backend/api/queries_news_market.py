"""
Query and analysis module for linked news and market data.
Use these queries to extract insights from collected news and price movements.
"""

import logging
from typing import Any

from backend.database import db_manager
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def get_news_price_correlation(
    symbol: str = "BTC",
    hours_before: int = 24,
    hours_after: int = 24,
    limit: int = 100
) -> list[dict[str, Any]]:
    """
    Get news articles linked with subsequent price movements.
    
    Args:
        symbol: BTC, ETH, or DOGE
        hours_before: Look back window before news
        hours_after: Look ahead window after news for price correlation
        limit: Maximum results to return
    
    Returns:
        List of news articles with price movements
    """
    async with db_manager.session_factory() as session:
        result = await session.execute(text("""
            SELECT
                na.id,
                na.ts as news_time,
                na.title,
                na.source_name,
                nml.lagged_price_change_pct as price_change_pct,
                na.mentioned_coins,
                na.primary_symbol,
                nml.time_window,
                EXTRACT(EPOCH FROM (NOW() - na.ts)) / 3600 as hours_ago,
                na.metadata
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            WHERE nml.symbol = :symbol
            AND na.ts > NOW() - INTERVAL '1' DAY * :hours_before
            AND (nml.lagged_price_change_pct IS NOT NULL OR TRUE)
            ORDER BY na.ts DESC
            LIMIT :limit
        """), {
            "symbol": symbol,
            "hours_before": hours_before,
            "limit": limit
        })
        
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_top_news_sources(symbol: str = "BTC", days: int = 7) -> list[dict[str, Any]]:
    """Get top news sources for a given symbol."""
    async with db_manager.session_factory() as session:
        result = await session.execute(text("""
            SELECT
                na.source_name,
                COUNT(*) as article_count,
                COUNT(DISTINCT DATE(na.ts)) as days_active,
                AVG(nml.lagged_price_change_pct) as avg_price_change_pct,
                MIN(na.ts) as first_article,
                MAX(na.ts) as latest_article
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            WHERE nml.symbol = :symbol
            AND na.ts > NOW() - INTERVAL '1' DAY * :days
            GROUP BY na.source_name
            ORDER BY article_count DESC
            LIMIT 10
        """), {
            "symbol": symbol,
            "days": days
        })
        
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_high_impact_news(
    symbol: str = "BTC",
    min_price_change: float = 5.0,
    days: int = 30
) -> list[dict[str, Any]]:
    """
    Get news articles associated with significant price movements.
    
    Args:
        symbol: BTC, ETH, or DOGE
        min_price_change: Minimum absolute price change percentage
        days: Days to look back
    """
    async with db_manager.session_factory() as session:
        result = await session.execute(text("""
            SELECT
                na.ts,
                na.title,
                na.source_name,
                na.content,
                nml.lagged_price_change_pct,
                na.mentioned_coins,
                CASE 
                    WHEN nml.lagged_price_change_pct > :min_change THEN 'Bullish'
                    WHEN nml.lagged_price_change_pct < -:min_change THEN 'Bearish'
                    ELSE 'Neutral'
                END as impact_direction
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            WHERE nml.symbol = :symbol
            AND na.ts > NOW() - INTERVAL '1' DAY * :days
            AND (
                nml.lagged_price_change_pct > :min_change
                OR nml.lagged_price_change_pct < -:min_change
            )
            ORDER BY ABS(nml.lagged_price_change_pct) DESC
            LIMIT 100
        """), {
            "symbol": symbol,
            "min_change": min_price_change,
            "days": days
        })
        
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_hourly_news_price_correlation(
    symbol: str = "BTC",
    hours: int = 168  # 1 week
) -> list[dict[str, Any]]:
    """
    Get hourly statistics of news articles and related price movements.
    """
    async with db_manager.session_factory() as session:
        result = await session.execute(text("""
            SELECT
                DATE_TRUNC('hour', na.ts) as hour,
                COUNT(*) as news_count,
                AVG(nml.lagged_price_change_pct) as avg_price_change_pct,
                MIN(nml.lagged_price_change_pct) as min_price_change_pct,
                MAX(nml.lagged_price_change_pct) as max_price_change_pct,
                STRING_AGG(DISTINCT na.source_name, ', ') as sources,
                COUNT(DISTINCT na.id) as unique_articles
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            WHERE nml.symbol = :symbol
            AND na.ts > NOW() - INTERVAL '1' HOUR * :hours
            GROUP BY DATE_TRUNC('hour', na.ts)
            ORDER BY hour DESC
            LIMIT 200
        """), {
            "symbol": symbol,
            "hours": hours
        })
        
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_sentiment_keywords(symbol: str = "BTC", limit: int = 50) -> list[dict[str, Any]]:
    """
    Get most common keywords in news for a symbol.
    (Requires keyword extraction implementation)
    """
    async with db_manager.session_factory() as session:
        result = await session.execute(text("""
            SELECT
                na.mentioned_coins,
                COUNT(*) as frequency,
                COUNT(DISTINCT DATE(na.ts)) as days_mentioned
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            WHERE nml.symbol = :symbol
            GROUP BY na.mentioned_coins
            ORDER BY frequency DESC
            LIMIT :limit
        """), {
            "symbol": symbol,
            "limit": limit
        })
        
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_news_for_price_analysis(
    symbol: str = "BTC",
    start_date: str = None,
    end_date: str = None
) -> list[dict[str, Any]]:
    """
    Get all news for a date range for feature engineering and analysis.
    Returns raw data suitable for ML model training.
    """
    async with db_manager.session_factory() as session:
        query = """
            SELECT
                na.id,
                na.ts,
                na.title,
                na.content,
                na.source_name,
                na.author,
                na.mentioned_coins,
                na.primary_symbol,
                nml.lagged_price_change_pct,
                pd.close as price_at_news,
                pd.volume as volume_at_news,
                jsonb_pretty(na.metadata) as metadata
            FROM news_articles na
            JOIN news_market_linkage nml ON na.id = nml.news_id
            LEFT JOIN price_data pd ON (
                pd.symbol = nml.symbol || 'USDT'
                AND pd.interval = '1h'
                AND pd.ts >= na.ts
                AND pd.ts <= na.ts + INTERVAL '1 hour'
            )
            WHERE nml.symbol = :symbol
        """
        
        params = {"symbol": symbol}
        
        if start_date:
            query += " AND na.ts >= :start_date"
            params["start_date"] = start_date
        
        if end_date:
            query += " AND na.ts <= :end_date"
            params["end_date"] = end_date
        
        query += " ORDER BY na.ts DESC LIMIT 10000"
        
        result = await session.execute(text(query), params)
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


async def get_statistics_summary(hours: int = 24) -> dict[str, Any]:
    """Get summary statistics of collected news."""
    async with db_manager.session_factory() as session:
        # Total articles
        result = await session.execute(text("""
            SELECT COUNT(*) FROM news_articles 
            WHERE ts > NOW() - INTERVAL '1 hour' * :hours
        """), {"hours": hours})
        total_articles = result.scalar() or 0
        
        # By symbol
        result = await session.execute(text("""
            SELECT symbol, COUNT(*) as count
            FROM news_market_linkage
            WHERE ts > NOW() - INTERVAL '1 hour' * :hours
            GROUP BY symbol
        """), {"hours": hours})
        by_symbol = {row[0]: row[1] for row in result.fetchall()}
        
        # Average price changes
        result = await session.execute(text("""
            SELECT symbol, AVG(lagged_price_change_pct) as avg_change
            FROM news_market_linkage
            WHERE ts > NOW() - INTERVAL '1 hour' * :hours
            AND lagged_price_change_pct IS NOT NULL
            GROUP BY symbol
        """), {"hours": hours})
        avg_changes = {row[0]: row[1] for row in result.fetchall()}
        
        # Sources
        result = await session.execute(text("""
            SELECT source_name, COUNT(*) as count
            FROM news_articles
            WHERE ts > NOW() - INTERVAL '1 hour' * :hours
            GROUP BY source_name
            ORDER BY count DESC
        """), {"hours": hours})
        sources = {row[0]: row[1] for row in result.fetchall()}
        
        return {
            "period_hours": hours,
            "total_articles": total_articles,
            "articles_by_symbol": by_symbol,
            "average_price_changes": avg_changes,
            "top_sources": sources
        }


# Example usage in scripts or notebooks
if __name__ == "__main__":
    import asyncio
    
    async def examples():
        # Get BTC news with price movements
        news = await get_news_price_correlation("BTC", limit=10)
        print(f"BTC news with price correlations: {len(news)} found")
        
        # Get top sources
        sources = await get_top_news_sources("BTC", days=7)
        print(f"\nTop BTC news sources (7 days):")
        for source in sources[:5]:
            print(f"  {source['source_name']}: {source['article_count']} articles")
        
        # Get high impact news
        high_impact = await get_high_impact_news("BTC", min_price_change=2.0)
        print(f"\nHigh impact BTC news: {len(high_impact)} found")
        
        # Statistics
        stats = await get_statistics_summary(hours=24)
        print(f"\n24-hour statistics: {stats['total_articles']} articles")
        
        await db_manager.close()
    
    asyncio.run(examples())
