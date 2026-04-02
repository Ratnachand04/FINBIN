# NewsAPI Token Optimization Strategy

## Token Budget
- **Total Tokens**: 2000
- **Time Window**: 1 day
- **Target Assets**: Bitcoin (BTC), Ethereum (ETH), Dogecoin (DOGE)

## Cost Structure (Recent Content - Last 30 days)
- **Article Search**: 1 token per query
- **Maximum Possible Searches**: 2000

## Optimized Query Strategy

### Phase 1: Core Asset Searches (10 tokens)
```
1. "bitcoin" - 1 token
2. "Bitcoin" - 1 token  
3. "BTC price" - 1 token
4. "BTC price" - 1 token
5. "bitcoin news" - 1 token
6. "Bitcoin news" - 1 token
7. "BTC market" - 1 token
8. "BTC market" - 1 token
9. "bitcoin mining" - 1 token
10. "Bitcoin network" - 1 token
```

### Phase 2: Event/Catalyst Searches (30 tokens)
```
- Bitcoin halving
- Bitcoin upgrade
- Bitcoin ETF
- Bitcoin staking
- Bitcoin regulation
- Bitcoin security
- Bitcoin adoption
- Bitcoin DeFi
- Bitcoin transaction
- Bitcoin layer2
- And 20 more variations...
```

### Phase 3: Market Sentiment Searches (60 tokens)
```
- "bitcoin bull" / "bitcoin bear"
- "Bitcoin bull" / "Bitcoin bear"
- Market crash/surge scenarios
- Regulatory news
- Exchange listings
- Developer updates
```

### Phase 4: Maximum Volume Collection (1900 tokens)
**Target**: 1900+ searches remaining for high-volume, lower-specificity queries:
```
- Rotating through keywords:
  - bitcoin, Bitcoin, BTC, ethereum, Ethereum, ETH, dogecoin, Dogecoin, DOGE
  - Supporting context: crypto, blockchain, defi, NFT, Web3
  - Exchange names: Binance, Coinbase, Kraken
  - Time variations: "today", "this week", "market data"
```

## Implementation Details

### Query Patterns for BTC, ETH, DOGE Focus
```
# Core searches (sorted by relevance)
bitcoin, Bitcoin, BTC, ethereum, Ethereum, ETH, dogecoin, Dogecoin, DOGE

# Specific events
"bitcoin halving", "ethereum upgrade", "dogecoin news"

# Market related
"crypto market", "bitcoin price", "ethereum DeFi", "dogecoin community"

# Sentiment & news types
"bullish", "bearish", "regulation", "adoption", "partnerships"
```

### Results Per Search
- **pageSize**: 100 (maximum)
- **Articles per query**: ~50-100 articles
- **Records per day**: 100,000+ articles possible

## Token Efficiency Calculation

| Phase | Queries | Tokens | Articles/Query | Total Articles |
|-------|---------|--------|----------------|-----------------|
| Core (3 assets) | 300 | 300 | 80 | 24,000 |
| Events (3 assets) | 400 | 400 | 80 | 32,000 |
| Sentiment (3 assets) | 500 | 500 | 80 | 40,000 |
| Volume (3 assets) | 800 | 800 | 80 | 64,000 |
| **Total** | **2,000** | **2,000** | | **160,000+** |

## Implementation Recommendations

1. **Use paginated searches**: Each search can return up to 100 articles
2. **Rotate keywords**: Distribute queries across BTC variations
3. **Time distribution**: Spread requests throughout the day to capture different time zones
4. **Deduplication**: Store URL hash to avoid duplicates across queries
5. **Priority ordering**: Bitcoin & Bitcoin focus (80%), other context (20%)

## Sample Optimized Query List (2000 queries)

### Bitcoin-focused: 700 queries
```python
btc_keywords = [
    "bitcoin", "BTC", "bitcoin price", "bitcoin mining",
    "bitcoin news", "bitcoin market", "bitcoin adoption",
    "bitcoin regulation", "bitcoin ETF", "bitcoin halving",
    # ... plus 690 more variations
]
```

### Ethereum-focused: 700 queries  
```python
eth_keywords = [
    "ethereum", "ETH", "ethereum price", "ethereum upgrade",
    "ethereum news", "ethereum DeFi", "ethereum staking",
    "ethereum layer2", "ethereum security", "ethereum adoption",
    # ... plus 690 more variations
]
```

### Dogecoin-focused: 600 queries
```python
doge_keywords = [
    "dogecoin", "DOGE", "dogecoin price", "dogecoin news",
    "dogecoin community", "dogecoin partnerships", "dogecoin market",
    "shiba inu", "DOGE trends", "dogecoin adoption",
    # ... plus 590 more variations
]
```

## Expected Results
- **Total Articles**: 160,000+
- **Tokens Used**: 2,000
- **Average Cost per Article**: 0.0125 tokens
- **Coverage**: 30-day rolling window for BTC & BTC news
