# News Collection & Market Data Linking Guide

Complete guide to pull maximum Bitcoin news and link with market data using TimescaleDB.

## Overview

This system enables you to:
1. **Pull maximum news** using 2000 token budget from NewsAPI
2. **Store efficiently** using TimescaleDB hypertables
3. **Link with market data** to correlate news with price movements
4. **Analyze sentiment** and identify trading signals

## Architecture

```
NewsAPI (2000 tokens/day)
    ↓
[News Collector] → 100,000+ articles/day for BTC & BTC
    ↓
TimescaleDB Hypertable: news_articles
    ↓
[Linking Pipeline] → Match news with price data by time/symbol
    ↓
TimescaleDB Hypertable: news_market_linkage
    ↓
[Analysis Queries] → Correlations, sentiment, signals
```

## Quick Start

### 1. Initialize Database

```powershell
# Apply TimescaleDB schema with optimized news tables
cd E:\BINFIN
Get-Content .\database\schema.sql | docker compose exec -T -e PGPASSWORD=binfin postgres psql -v ON_ERROR_STOP=1 -U binfin -d binfin
```

### 2. Configure Environment

Your `.env` already has `NEWSAPI_KEY=ad1b29db-e847-4253-88ad-a48e75a5ed96`

### 3. Run Full Pipeline

```powershell
# Pull news, link with market data, calculate correlations
cd E:\BINFIN
python .\scripts\pull_news_and_link_market_data.py
```

**Expected Output:**
```
PHASE 1: NewsAPI Collection (Optimized Queries)
✓ Collected 100,000+ articles from NewsAPI

PHASE 2: RSS Feed Collection (Supplementary)
✓ Collected 5,000+ articles from RSS feeds

PHASE 3: Link News with Market Data
✓ Created 105,000+ market linkage entries

PHASE 4: Calculate Price Correlations
✓ Updated price correlations for 50,000+ linkages

DATA COLLECTION SUMMARY
Total news articles collected: 105,000+
```

## Database Schema

### news_articles (TimescaleDB Hypertable)
```sql
CREATE TABLE news_articles (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,              -- Time-series key (optimized)
    url_hash TEXT UNIQUE NOT NULL,         -- Deduplication
    source_name TEXT NOT NULL,             -- CoinDesk, CoinTelegraph, etc.
    title TEXT NOT NULL,                   -- Article headline
    content TEXT,                          -- Full article text
    author TEXT,                           -- Author name
    url TEXT,                              -- Original URL
    image_url TEXT,                        -- Featured image
    mentioned_coins TEXT[],                -- ['BTC', 'ETH', 'DOGE']
    primary_symbol TEXT,                   -- BTC or BTC (main focus)
    sentiment_keywords TEXT[],             -- ['bullish', 'crash', 'regulation']
    metadata JSONB,                        -- Additional data
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Optimized indexes
CREATE INDEX idx_news_articles_ts DESC
CREATE INDEX idx_news_articles_symbol_ts (primary_symbol, ts DESC)
CREATE INDEX idx_news_articles_source_ts (source_name, ts DESC)
CREATE INDEX idx_news_articles_mentioned_coins GIN(mentioned_coins)
```

### news_market_linkage (TimescaleDB Hypertable)
```sql
CREATE TABLE news_market_linkage (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,              -- Time-series key
    news_id BIGINT REFERENCES news_articles(id),
    symbol TEXT NOT NULL,                  -- Symbol linked
    time_window TEXT DEFAULT '1h',         -- Correlation window
    price_correlation DOUBLE PRECISION,    -- -1 to 1
    lagged_price_change_pct DOUBLE PRECISION,  -- % change after news
    metadata JSONB
);

-- Links each article to BTC/ETH/DOGE with price movements
-- Many-to-many: one article can mention multiple coins
```

## Data Statistics

### Daily Collection Capacity (2000 tokens)
- **Per Search**: 1 token (recent content, last 30 days)
- **Results/Search**: 50-100 articles
- **Total Searches**: 2,000
- **Total Articles**: 100,000 - 200,000+
- **BTC Coverage**: 50% of queries (1,000 searches)
- **BTC Coverage**: 50% of queries (1,000 searches)

### Query Distribution
```
Bitcoin (1000 queries, ~50,000 articles):
  - Core BTC: "bitcoin", "BTC", "Bitcoin price", etc.
  - BTC Events: "Bitcoin halving", "Bitcoin ETF", Mining, Regulation
  - BTC Sentiment: "bitcoin bull", "bitcoin crash", etc.
  
Bitcoin (1000 queries, ~50,000 articles):
  - Core BTC: "Bitcoin", "BTC", "Bitcoin price", etc.
  - BTC Events: "staking", "DeFi", "layer2", "Merge", etc.
  - BTC Sentiment: "Bitcoin bull", "Bitcoin crash", etc.

Market Context (500 queries from RSS & supplementary):
  - Exchange info, regulatory news, network events
```

## Usage Examples

### Query 1: Get Recent News with Price Impact

```python
import asyncio
from backend.api.queries_news_market import get_news_price_correlation

async def analyze_btc_news():
    # News from last 24 hours with price movements
    news = await get_news_price_correlation(
        symbol="BTC",
        hours_before=24,
        hours_after=24,
        limit=100
    )
    
    for article in news:
        print(f"{article['news_time']}: {article['title']}")
        print(f"  Source: {article['source_name']}")
        print(f"  Price change: {article['price_change_pct']:.2f}%")
        print()

asyncio.run(analyze_btc_news())
```

### Query 2: Find High-Impact News

```python
from backend.api.queries_news_market import get_high_impact_news

async def find_signals():
    # News that caused >5% price movements
    high_impact = await get_high_impact_news(
        symbol="BTC",
        min_price_change=5.0,
        days=30
    )
    
    bullish = [x for x in high_impact if x['impact_direction'] == 'Bullish']
    bearish = [x for x in high_impact if x['impact_direction'] == 'Bearish']
    
    print(f"Bullish news (price +5%+): {len(bullish)}")
    print(f"Bearish news (price -5%-): {len(bearish)}")

asyncio.run(find_signals())
```

### Query 3: Top News Sources

```python
from backend.api.queries_news_market import get_top_news_sources

async def source_analysis():
    sources = await get_top_news_sources("BTC", days=7)
    
    for source in sources:
        print(f"{source['source_name']}")
        print(f"  Articles: {source['article_count']}")
        print(f"  Avg price change: {source['avg_price_change_pct']:.2f}%")

asyncio.run(source_analysis())
```

### Query 4: Hourly News Statistics

```python
from backend.api.queries_news_market import get_hourly_news_price_correlation

async def hourly_stats():
    stats = await get_hourly_news_price_correlation("BTC", hours=168)
    
    for hour in stats:
        print(f"{hour['hour']}")
        print(f"  News count: {hour['news_count']}")
        print(f"  Avg price change: {hour['avg_price_change_pct']:.2f}%")

asyncio.run(hourly_stats())
```

### Query 5: Export for Machine Learning

```python
from backend.api.queries_news_market import get_news_for_price_analysis
import pandas as pd

async def prepare_ml_data():
    # Get all news and prices for training
    data = await get_news_for_price_analysis(
        symbol="BTC",
        start_date="2024-01-01",
        end_date="2024-03-01"
    )
    
    # Convert to DataFrame for ML
    df = pd.DataFrame(data)
    df.to_csv("btc_news_price_data.csv", index=False)
    print(f"Exported {len(df)} records for ML training")

asyncio.run(prepare_ml_data())
```

## Advanced Features

### 1. Real-time News Streaming
Monitor news as it arrives and execute trades:

```python
from backend.collectors.news_collector import NewsCollector
import asyncio

async def monitor_news():
    async with NewsCollector() as collector:
        while True:
            articles = await collector.collect_from_newsapi()
            for article in articles:
                if article['primary_symbol'] == 'BTC':
                    if 'crashed' in article['title'].lower():
                        print(f"ALERT: Bearish BTC news: {article['title']}")
            await asyncio.sleep(300)  # Check every 5 minutes
```

### 2. Sentiment Analysis
Classify articles as bullish/bearish/neutral:

```python
from transformers import pipeline

sentiment_analyzer = pipeline("sentiment-analysis", 
    model="distilbert-base-uncased-finetuned-sst-2-english")

async def add_sentiment():
    data = await get_news_for_price_analysis("BTC")
    
    for article in data:
        # Analyze title + first 100 words
        text = f"{article['title']}. {article['content'][:500]}"
        result = sentiment_analyzer(text[:512])
        
        # Save sentiment to database
        # UPDATE news_articles SET sentiment_keywords = ...
```

### 3. Price Correlation Strength
Calculate statistical significance:

```sql
-- Find news-price correlations by time window
SELECT 
    primary_symbol,
    time_window,
    COUNT(*) as sample_size,
    AVG(lagged_price_change_pct) as mean_price_change,
    STDDEV(lagged_price_change_pct) as std_dev,
    CORR(lagged_price_change_pct, 1) as correlation  -- Add features for CORR
FROM news_market_linkage
GROUP BY primary_symbol, time_window
ORDER BY ABS(mean_price_change) DESC;
```

## Monitoring

### Check Collection Progress

```python
from datetime import datetime, UTC
from backend.database import db_manager

async def monitor():
    async with db_manager.session_factory() as session:
        # Get today's requests
        result = await session.execute(
            text("""
                SELECT COUNT(*) FROM news_articles 
                WHERE ts > NOW() - INTERVAL '1 day'
            """)
        )
        today_articles = result.scalar()
        print(f"Today's articles: {today_articles:,}")
```

### Set Up Alerts

```python
# Alert when news impacts price >5%
SELECT * FROM news_market_linkage
WHERE lagged_price_change_pct > 5
AND ts > NOW() - INTERVAL '1 hour'
ORDER BY lagged_price_change_pct DESC;
```

## Performance Tips

1. **Use Hypertables**: Queries on `ts` are automatically optimized
2. **Filter by Symbol First**: Always include `WHERE symbol = 'BTC'` 
3. **Batch Inserts**: Save_to_db handles deduplication efficiently
4. **Continuous Aggregates**: Set up hourly/daily summaries for dashboards

## Troubleshooting

### No articles inserted?

```bash
# Check if API key is working
python -c "import os; print(os.getenv('NEWSAPI_KEY'))"

# Check API rate limits (in Redis)
redis-cli GET newsapi:requests:$(date +%Y%m%d)
```

### Correlation is NULL?

- Need price_data records for the time window
- Check: `SELECT * FROM price_data WHERE ts >= article_ts - '2h'`

### Query slow?

```sql
-- Check index usage
EXPLAIN ANALYZE
SELECT * FROM news_articles 
WHERE primary_symbol = 'BTC' 
AND ts > NOW() - INTERVAL '7 days'
ORDER BY ts DESC;

-- Rebuild hypertable chunks
SELECT reorder_chunks('news_articles');
```

## Next Steps

1. ✅ API key configured
2. ✅ Schema set up with hypertables
3. ✅ News collection script ready
4. ✅ Query library available
5. 📋 **Integrate with Signal Generator** (see `backend/signals/signal_generator.py`)
6. 📋 **Set up Real-time Dashboard** (see `frontend/pages/`)
7. 📋 **Train Sentiment Model** (see `backend/ml/`)
8. 📋 **Run Backtests** with news features (see `backend/backtest/`)

## References

- **Schema**: [database/schema.sql](../database/schema.sql)
- **Collection Script**: [scripts/pull_news_and_link_market_data.py](../scripts/pull_news_and_link_market_data.py)
- **Query Library**: [backend/api/queries_news_market.py](../backend/api/queries_news_market.py)
- **News Collector**: [backend/collectors/news_collector.py](../backend/collectors/news_collector.py)
- **API Docs**: https://newsapi.org/docs

---

**Updated**: April 1, 2026
**Token Budget**: 2,000 per day
**Expected Articles/Day**: 100,000+
