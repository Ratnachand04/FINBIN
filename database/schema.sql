CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS price_data (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open DOUBLE PRECISION NOT NULL,
    high DOUBLE PRECISION NOT NULL,
    low DOUBLE PRECISION NOT NULL,
    close DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION NOT NULL,
    quote_volume DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    UNIQUE(symbol, interval, ts)
);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    source_type TEXT NOT NULL,
    sentiment_label TEXT,
    sentiment_score DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS reddit_posts (
    id BIGSERIAL PRIMARY KEY,
    post_id TEXT UNIQUE NOT NULL,
    subreddit TEXT NOT NULL,
    title TEXT,
    body TEXT,
    author TEXT,
    score INT DEFAULT 0,
    mentioned_coins TEXT[] DEFAULT ARRAY[]::TEXT[],
    created_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS news_articles (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    url_hash TEXT UNIQUE NOT NULL,
    source_name TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT,
    author TEXT,
    url TEXT,
    image_url TEXT,
    mentioned_coins TEXT[] DEFAULT ARRAY[]::TEXT[],
    primary_symbol TEXT,
    sentiment_keywords TEXT[],
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Link table for many-to-many relationship between news and price data
CREATE TABLE IF NOT EXISTS news_market_linkage (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    news_id BIGINT NOT NULL REFERENCES news_articles(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    time_window TEXT NOT NULL DEFAULT '1h',
    price_correlation DOUBLE PRECISION,
    lagged_price_change_pct DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS whale_transactions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    chain TEXT NOT NULL,
    tx_hash TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    amount_usd DOUBLE PRECISION NOT NULL,
    flow_direction TEXT,
    is_whale BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS predictions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    horizon TEXT NOT NULL,
    predicted_price DOUBLE PRECISION,
    predicted_direction TEXT,
    confidence DOUBLE PRECISION,
    model_name TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    strength DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    explanation TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    win_rate DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    sortino_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    total_return_pct DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB
);

CREATE INDEX IF NOT EXISTS idx_price_data_symbol_interval_ts ON price_data(symbol, interval, ts DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_ts ON sentiment_scores(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_whale_symbol_ts ON whale_transactions(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol_ts ON predictions(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals(symbol, ts DESC);

-- News article indexes for efficient time-series queries and linking
CREATE INDEX IF NOT EXISTS idx_news_articles_ts_desc ON news_articles(ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_symbol_ts ON news_articles(primary_symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_source_ts ON news_articles(source_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_articles_mentioned_coins ON news_articles USING GIN(mentioned_coins);
CREATE INDEX IF NOT EXISTS idx_news_articles_url_hash ON news_articles(url_hash);

-- Link table indexes for efficient joins
CREATE INDEX IF NOT EXISTS idx_news_market_linkage_symbol_ts ON news_market_linkage(symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_market_linkage_news_id ON news_market_linkage(news_id);
CREATE INDEX IF NOT EXISTS idx_news_market_linkage_time_window ON news_market_linkage(symbol, time_window, ts DESC);

CREATE MATERIALIZED VIEW IF NOT EXISTS sentiment_agg_1h AS
SELECT
    date_trunc('hour', ts) AS bucket,
    symbol,
    avg(sentiment_score) AS avg_sentiment,
    count(*) AS sample_count
FROM sentiment_scores
GROUP BY date_trunc('hour', ts), symbol;

-- Timescale retention policies are disabled here because these tables are currently regular PostgreSQL tables.
