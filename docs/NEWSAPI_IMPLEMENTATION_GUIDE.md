# NewsAPI Token Optimization - Implementation Guide

## Current Setup

Your **NEWSAPI_KEY** is stored in `.env` and configured for maximum data collection.

## Changes Made

### 1. **Increased Daily Token Budget** (100 → 2000)
- NewsAPI policy change: Article search (last 30 days) = **1 token per query**
- Previous limit: 100 requests/day = 100 tokens
- **New limit: 2000 requests/day = 2000 tokens**
- Updated in: `backend/collectors/news_collector.py` line ~110

### 2. **Optimized Query Strategy** (150 targeted searches)
Replaced single broad query with 150 focused queries:
- **Bitcoin-focused**: 50 queries
- **Ethereum-focused**: 50 queries  
- **Dogecoin-focused**: 30 queries
- **Market context**: 20 queries

### 3. **Deduplication System**
- Tracks seen URLs to prevent duplicate collection
- Reduces database storage and processing overhead

## Expected Output

| Metric | Value |
|--------|-------|
| **Queries per day** | 2,000 |
| **Tokens consumed** | 2,000 |
| **Articles per query** | 50-100 |
| **Total articles collected** | 100,000 - 200,000+ |
| **Data coverage** | Last 30 days |
| **Primary focus** | Bitcoin (35%), Ethereum (35%), Dogecoin (30%) |

## How to Run

### Full Collection Cycle
```bash
cd backend
python -m collectors.news_collector  # Runs automated collection
```

### Check Token Usage
Monitor daily token consumption in Redis:
```python
from backend.database import db_manager
from datetime import datetime, UTC

day_key = datetime.now(UTC).strftime("%Y%m%d")
key = f"newsapi:requests:{day_key}"
count = db_manager.redis_client.get(key)  # Shows requests used today
```

### View Collected Articles
```python
from backend.database import db_manager

# Query collected articles
articles = db_manager.execute(
    "SELECT * FROM articles WHERE source_name IN ('coindesk', 'cointelegraph', ...) "
    "ORDER BY published_at DESC LIMIT 1000"
)
```

## Optimization Tips

### Phase-based Collection (Recommended)
Schedule collection in phases to track progress:

```
Phase 1 (Hour 1):   Core BTC queries (100 queries, 100 tokens)
Phase 2 (Hour 2):   Market context queries (50 queries, 50 tokens)
Phase 3 (Hour 3):   Exchange queries (50 queries, 50 tokens)
Phase 4 (Hours 4-8): Rotation queries (1,800 queries, 1,800 tokens)
```

### Progressive Keyword Expansion
If you need more data variation:
```python
# Additional query variations to add to OPTIMIZED_QUERIES
additional_queries = [
    # Time-based
    "bitcoin this week", "Bitcoin this week",
    "bitcoin today", "Bitcoin today",
    
    # Sentiment
    "bitcoin bullish", "Bitcoin bullish",
    "bitcoin bearish", "Bitcoin bearish",
    
    # Technical
    "bitcoin technical analysis", "Bitcoin technical analysis",
    "bitcoin chart pattern", "Bitcoin chart pattern",
]
```

## Query Cost Details

### Article Search (Last 30 days)
- **Cost**: 1 token per search
- **Results**: up to 100 articles per search
- **Your allocation**: 2,000 searches available

### Premium Queries NOT Using (Save for important moments)
- Historical search (since 2014): 5 tokens per year
- Event search: 5-20 tokens depending on range
- **These should be avoided** - not cost-effective

## Monitoring & Alerts

Add alerts for:
1. **Daily quota warning** (1,500+ tokens used)
2. **Collection failure** (missing articles from queries)
3. **API rate limit** (429 responses)
4. **Duplicate article rate** (>50% duplicates = adjust queries)

## Next Steps

1. ✅ API key configured in `.env`
2. ✅ News collector optimized for 2,000 token budget
3. ✅ Deduplication system active
4. 📋 Schedule collection worker (see backend/workers/)
5. 📋 Set up monitoring dashboard (see monitoring/grafana/)
6. 📋 Export collected data (see scripts/export_*_sql)

---

**Last Updated**: April 1, 2026
**Token Budget**: 2,000 tokens/day
**Primary Assets**: Bitcoin, Bitcoin
