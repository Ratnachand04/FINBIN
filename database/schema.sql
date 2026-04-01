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
SELECT create_hypertable('price_data', 'ts', if_not_exists => TRUE);

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
SELECT create_hypertable('sentiment_scores', 'ts', if_not_exists => TRUE);

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
    url_hash TEXT UNIQUE NOT NULL,
    source_name TEXT,
    title TEXT NOT NULL,
    content TEXT,
    published_at TIMESTAMPTZ,
    mentioned_coins TEXT[] DEFAULT ARRAY[]::TEXT[],
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
SELECT create_hypertable('whale_transactions', 'ts', if_not_exists => TRUE);

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
SELECT create_hypertable('predictions', 'ts', if_not_exists => TRUE);

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
SELECT create_hypertable('signals', 'ts', if_not_exists => TRUE);

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

CREATE MATERIALIZED VIEW IF NOT EXISTS sentiment_agg_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    symbol,
    avg(sentiment_score) AS avg_sentiment,
    count(*) AS sample_count
FROM sentiment_scores
GROUP BY bucket, symbol;

SELECT add_continuous_aggregate_policy('sentiment_agg_1h',
    start_offset => INTERVAL '2 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '15 minutes');

SELECT add_retention_policy('price_data', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('sentiment_scores', INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('whale_transactions', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('predictions', INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('signals', INTERVAL '365 days', if_not_exists => TRUE);
