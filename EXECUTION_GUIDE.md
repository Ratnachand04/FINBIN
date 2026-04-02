# News Collection Execution Guide

## Step 1: Verify Prerequisites

```bash
# Check Python environment
python --version
# Expected: Python 3.10+

# Check PostgreSQL connection
psql -U postgres -d binfin -c "SELECT version();"
# Expected: PostgreSQL with TimescaleDB extension

# Check API key
echo $NEWSAPI_KEY
# Expected: ad1b29db-e847-4253-88ad-a48e75a5ed96
```

## Step 2: Initialize Database Schema

```powershell
# Apply TimescaleDB schema updates
cd E:\BINFIN
Get-Content .\database\schema.sql | docker compose exec -T -e PGPASSWORD=binfin postgres psql -v ON_ERROR_STOP=1 -U binfin -d binfin

# Verify tables created
docker compose exec -T -e PGPASSWORD=binfin postgres psql -U binfin -d binfin -c "
  SELECT hypertable_name, time_column_name 
  FROM timescaledb_information.hypertables 
  WHERE hypertable_name IN ('news_articles', 'news_market_linkage', 'price_data');
"
```

## Step 3: Run Collection Pipeline

### Option A: Full Automated Pipeline (Recommended)

```powershell
cd E:\BINFIN

# Run complete pipeline: collect → link → correlate
python .\scripts\pull_news_and_link_market_data.py

# Expected runtime: 30-60 minutes (pulling 100,000+ articles)
# Output: Progress logs + summary statistics
```

### Option B: Manual Steps

If you want to run each phase separately:

```python
# File: scripts/manual_collection.py
import asyncio
from scripts.pull_news_and_link_market_data import (
    validate_schema,
    collect_maximum_news,
    link_news_with_market_data,
    calculate_price_correlation,
    generate_summary
)

async def run_phases():
    # Phase 1
    await validate_schema()
    
    # Phase 2
    count = await collect_maximum_news()
    print(f"Collected {count:,} articles")
    
    # Phase 3
    linked = await link_news_with_market_data()
    print(f"Linked {linked:,} articles to market data")
    
    # Phase 4
    await calculate_price_correlation()
    
    # Summary
    await generate_summary()

asyncio.run(run_phases())
```

## Step 4: Monitor Collection Progress

```bash
# In another terminal, monitor real-time progress
watch -n 5 "
  psql -U postgres -d binfin -c \"
    SELECT 
      COUNT(*) as total_articles,
      MAX(ts) as latest_article 
    FROM news_articles;
  \"
"
```

## Step 5: Analyze Collected Data

### Query 1: Basic Statistics

```sql
SELECT 
  COUNT(*) as total_articles,
  COUNT(DISTINCT source_name) as unique_sources,
  COUNT(DISTINCT DATE(ts)) as days_covered,
  MIN(ts) as earliest,
  MAX(ts) as latest
FROM news_articles;
```

### Query 2: BTC vs BTC Split

```sql
SELECT 
  primary_symbol,
  COUNT(*) as article_count,
  COUNT(DISTINCT source_name) as sources,
  AVG(LENGTH(content)) as avg_content_length
FROM news_articles
WHERE primary_symbol IN ('BTC', 'BTC')
GROUP BY primary_symbol;
```

### Query 3: Top Sources

```sql
SELECT 
  source_name,
  COUNT(*) as article_count,
  COUNT(DISTINCT DATE(ts)) as days_active,
  MIN(ts) as first_article,
  MAX(ts) as latest_article
FROM news_articles
GROUP BY source_name
ORDER BY article_count DESC
LIMIT 10;
```

### Query 4: Articles with Price Correlation

```sql
SELECT 
  symbol,
  COUNT(*) as linkage_count,
  COUNT(CASE WHEN lagged_price_change_pct IS NOT NULL THEN 1 END) as correlated,
  AVG(lagged_price_change_pct) as avg_price_change_pct,
  STDDEV(lagged_price_change_pct) as price_change_stddev
FROM news_market_linkage
GROUP BY symbol;
```

## Step 6: Python Analysis

```python
#!/usr/bin/env python3
import asyncio
from backend.api.queries_news_market import (
    get_news_price_correlation,
    get_high_impact_news,
    get_top_news_sources,
    get_statistics_summary
)

async def analyze():
    # Summary
    stats = await get_statistics_summary(hours=24)
    print("24-Hour Summary:")
    print(f"  Total articles: {stats['total_articles']:,}")
    print(f"  By symbol: {stats['articles_by_symbol']}")
    
    # High impact news
    high_impact_btc = await get_high_impact_news("BTC", min_price_change=3.0)
    print(f"\nHigh impact BTC news (±3%+): {len(high_impact_btc)}")
    for article in high_impact_btc[:3]:
        print(f"  • {article['title'][:60]}...")
        print(f"    Impact: {article['lagged_price_change_pct']:.2f}%")
    
    # Top sources
    sources = await get_top_news_sources("BTC", days=7)
    print(f"\nTop BTC sources (7 days):")
    for source in sources[:5]:
        print(f"  • {source['source_name']}: {source['article_count']} articles")

asyncio.run(analyze())
```

## Performance Expectations

### Timing
- **Collection**: 30-60 min (API rate limits)
- **Linking**: 5-10 min
- **Correlation calc**: 10-20 min
- **Total**: 45-90 minutes

### Data Volume
- **Articles**: 100,000-200,000+
- **Linkages**: 150,000+
- **Database size**: 500MB-1GB

### Storage by Table
```sql
SELECT 
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables
WHERE tablename IN ('news_articles', 'news_market_linkage', 'price_data')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

## Troubleshooting

### Issue: "API key invalid"
```bash
# Verify API key
echo $NEWSAPI_KEY
printenv NEWSAPI_KEY

# Update .env if needed
echo "NEWSAPI_KEY=ad1b29db-e847-4253-88ad-a48e75a5ed96" >> /e/BINFIN/.env
```

### Issue: "Cannot connect to database"
```bash
# Check PostgreSQL
psql -U postgres -c "SELECT now();"

# Check TimescaleDB extension
psql -U postgres -d binfin -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
```

### Issue: "news_articles table not found"
```powershell
# Reapply schema
cd E:\BINFIN
Get-Content .\database\schema.sql | docker compose exec -T -e PGPASSWORD=binfin postgres psql -v ON_ERROR_STOP=1 -U binfin -d binfin

# Verify
docker compose exec -T -e PGPASSWORD=binfin postgres psql -U binfin -d binfin -c "SELECT * FROM news_articles LIMIT 1;"
```

### Issue: "Slow queries"
```sql
-- Rebuild chunks
SELECT reorder_chunks('news_articles');
SELECT reorder_chunks('news_market_linkage');

-- Refresh indexes
REINDEX TABLE news_articles;
REINDEX TABLE news_market_linkage;
```

## Production Deployment

### Schedule Daily Collection

```powershell
# Windows: Create scheduled task
# Task Scheduler → New Task → Run:
# powershell -ExecutionPolicy Bypass -File E:\BINFIN\scripts\run_news_pipeline.ps1
```

### Monitor Collection Health

```python
# Create monitoring dashboard
SELECT 
  DATE_TRUNC('day', ts) as day,
  COUNT(*) as articles_collected,
  COUNT(DISTINCT source_name) as sources,
  COUNT(DISTINCT primary_symbol) as symbols_covered
FROM news_articles
GROUP BY DATE_TRUNC('day', ts)
ORDER BY day DESC
LIMIT 30;
```

## Next: Integration with Trading System

Once collection is complete:

1. **Generate Trading Signals**
   - News sentiment + price correlation → Buy/Sell signals
   - See: `backend/signals/signal_generator.py`

2. **Feature Engineering**
   - Use news data in ML models
   - See: `backend/ml/feature_engineer.py`

3. **Backtesting**
   - Test news-based strategies
   - See: `backend/backtest/engine.py`

4. **Real-time Alerts**
   - Monitor live news feed
   - Execute trades based on signals
   - See: `backend/workers/`

---

**Ready to execute! Run:**
```powershell
cd E:\BINFIN
python .\scripts\pull_news_and_link_market_data.py
```
