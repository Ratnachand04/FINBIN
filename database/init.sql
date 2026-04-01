-- BINFIN PostgreSQL + TimescaleDB initialization schema
-- This file creates the full analytical schema for collection, ML, signaling,
-- monitoring, and backtesting workloads.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================================================
-- 1) reddit_data: Reddit posts/comments mentioning tracked coins
-- =========================================================
CREATE TABLE IF NOT EXISTS reddit_data (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('post', 'comment')),
    subreddit TEXT NOT NULL,
    reddit_id TEXT NOT NULL,
    author TEXT,
    title TEXT,
    body TEXT,
    score INTEGER,
    upvote_ratio DOUBLE PRECISION,
    num_comments INTEGER,
    permalink TEXT,
    mentioned_coins TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    sentiment_score DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, reddit_id)
);
COMMENT ON TABLE reddit_data IS 'Raw Reddit posts/comments with extracted coin mentions and sentiment.';
COMMENT ON COLUMN reddit_data.mentioned_coins IS 'Array of normalized ticker symbols extracted from post/comment text.';
COMMENT ON COLUMN reddit_data.metadata IS 'Additional extraction features such as language, entities, and moderation flags.';

-- =========================================================
-- 2) news_data: News articles and extracted entities
-- =========================================================
CREATE TABLE IF NOT EXISTS news_data (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    published_at TIMESTAMPTZ,
    source_name TEXT,
    author TEXT,
    title TEXT NOT NULL,
    description TEXT,
    content TEXT,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    mentioned_coins TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    extracted_entities JSONB NOT NULL DEFAULT '{}'::JSONB,
    sentiment_score DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (url_hash)
);
COMMENT ON TABLE news_data IS 'News articles enriched with entities, coin mentions, and sentiment outputs.';
COMMENT ON COLUMN news_data.extracted_entities IS 'JSONB map of extracted organizations, people, topics, and coin entities.';

-- =========================================================
-- 3) onchain_transactions: Whale movements and exchange flows
-- =========================================================
CREATE TABLE IF NOT EXISTS onchain_transactions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block_number BIGINT,
    symbol TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    amount NUMERIC(38, 18) NOT NULL,
    amount_usd NUMERIC(38, 8),
    is_whale BOOLEAN NOT NULL DEFAULT FALSE,
    whale_threshold_usd NUMERIC(38, 8),
    flow_direction TEXT CHECK (flow_direction IN ('to_exchange', 'from_exchange', 'wallet_to_wallet')),
    exchange_name TEXT,
    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chain, tx_hash)
);
COMMENT ON TABLE onchain_transactions IS 'On-chain transactions with whale flags and exchange inflow/outflow classification.';
COMMENT ON COLUMN onchain_transactions.flow_direction IS 'Direction classification for exchange flow tracking.';

-- =========================================================
-- 4) price_data: OHLCV series across intervals
-- =========================================================
CREATE TABLE IF NOT EXISTS price_data (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open NUMERIC(20, 10) NOT NULL,
    high NUMERIC(20, 10) NOT NULL,
    low NUMERIC(20, 10) NOT NULL,
    close NUMERIC(20, 10) NOT NULL,
    volume NUMERIC(30, 10) NOT NULL DEFAULT 0,
    quote_volume NUMERIC(30, 10),
    trade_count BIGINT,
    source TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, interval, ts)
);
COMMENT ON TABLE price_data IS 'Canonical OHLCV market data at multiple intervals (1m/5m/1h/1d, etc.).';

-- =========================================================
-- 5) sentiment_scores: Row-level sentiment outputs
-- =========================================================
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('reddit', 'news', 'onchain', 'other')),
    source_ref_id BIGINT,
    model_name TEXT NOT NULL,
    model_version TEXT,
    sentiment_label TEXT,
    sentiment_score DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION,
    processing_latency_ms INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE sentiment_scores IS 'Individual model inferences per data item and coin.';
COMMENT ON COLUMN sentiment_scores.source_ref_id IS 'Reference ID to source table row (reddit_data/news_data/onchain_transactions).';

-- =========================================================
-- 6) sentiment_aggregates: Time-window summary sentiment
-- =========================================================
CREATE TABLE IF NOT EXISTS sentiment_aggregates (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    window TEXT NOT NULL,
    symbol TEXT NOT NULL,
    sample_count INTEGER NOT NULL,
    avg_sentiment DOUBLE PRECISION NOT NULL,
    sentiment_stddev DOUBLE PRECISION,
    bullish_ratio DOUBLE PRECISION,
    bearish_ratio DOUBLE PRECISION,
    neutral_ratio DOUBLE PRECISION,
    weighted_score DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (window, symbol, ts)
);
COMMENT ON TABLE sentiment_aggregates IS 'Pre-aggregated sentiment metrics for fixed windows (e.g., 15m, 1h, 4h).';

-- =========================================================
-- 7) technical_indicators: Feature store for TA values
-- =========================================================
CREATE TABLE IF NOT EXISTS technical_indicators (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    sma_20 DOUBLE PRECISION,
    sma_50 DOUBLE PRECISION,
    ema_12 DOUBLE PRECISION,
    ema_26 DOUBLE PRECISION,
    rsi_14 DOUBLE PRECISION,
    macd DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,
    bb_upper DOUBLE PRECISION,
    bb_middle DOUBLE PRECISION,
    bb_lower DOUBLE PRECISION,
    atr_14 DOUBLE PRECISION,
    obv DOUBLE PRECISION,
    vwap DOUBLE PRECISION,
    adx_14 DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (symbol, interval, ts)
);
COMMENT ON TABLE technical_indicators IS 'Computed TA features (SMA/EMA/RSI/MACD/Bollinger/ATR/OBV/VWAP/ADX) per symbol/interval.';

-- =========================================================
-- 8) price_predictions: Ensemble model predictions and outcomes
-- =========================================================
CREATE TABLE IF NOT EXISTS price_predictions (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    prediction_horizon TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT,
    ensemble_id TEXT,
    predicted_price NUMERIC(20, 10),
    predicted_return DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    lower_bound NUMERIC(20, 10),
    upper_bound NUMERIC(20, 10),
    actual_price NUMERIC(20, 10),
    actual_return DOUBLE PRECISION,
    error_abs DOUBLE PRECISION,
    error_pct DOUBLE PRECISION,
    evaluated_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE price_predictions IS 'Model and ensemble predictions with realized outcomes for accuracy tracking.';

-- =========================================================
-- 9) trading_signals: Trade decisions and factor breakdowns
-- =========================================================
CREATE TABLE IF NOT EXISTS trading_signals (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    signal TEXT NOT NULL CHECK (signal IN ('BUY', 'SELL', 'HOLD', 'CLOSE')),
    strength DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    entry_price NUMERIC(20, 10),
    stop_loss NUMERIC(20, 10),
    take_profit NUMERIC(20, 10),
    horizon_minutes INTEGER,
    factors JSONB NOT NULL DEFAULT '{}'::JSONB,
    rationale TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE trading_signals IS 'Generated trading signals with full factor-level explainability payload.';
COMMENT ON COLUMN trading_signals.factors IS 'JSONB factor breakdown (sentiment, TA, onchain, regime, risk, model votes).';

-- =========================================================
-- 10) coin_configs: Per-coin runtime strategy configuration
-- =========================================================
CREATE TABLE IF NOT EXISTS coin_configs (
    symbol TEXT PRIMARY KEY,
    is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    min_signal_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.75,
    min_signal_strength DOUBLE PRECISION NOT NULL DEFAULT 7.0,
    max_position_size_pct DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    stop_loss_pct DOUBLE PRECISION,
    take_profit_pct DOUBLE PRECISION,
    tracked_intervals TEXT[] NOT NULL DEFAULT ARRAY['1m', '5m', '15m', '1h'],
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE coin_configs IS 'Per-coin configuration overrides for signals, risk, and interval tracking.';

-- =========================================================
-- 11) model_metadata: Model registry and deployment metadata
-- =========================================================
CREATE TABLE IF NOT EXISTS model_metadata (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_type TEXT NOT NULL,
    framework TEXT,
    artifact_path TEXT,
    trained_at TIMESTAMPTZ,
    deployed_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    train_dataset_start TIMESTAMPTZ,
    train_dataset_end TIMESTAMPTZ,
    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    parameters JSONB NOT NULL DEFAULT '{}'::JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, model_version)
);
COMMENT ON TABLE model_metadata IS 'Versioned ML model registry with metrics, params, and deployment state.';

-- =========================================================
-- 12) system_metrics: Infrastructure and service telemetry
-- =========================================================
CREATE TABLE IF NOT EXISTS system_metrics (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    service_name TEXT NOT NULL,
    host TEXT,
    cpu_percent DOUBLE PRECISION,
    memory_percent DOUBLE PRECISION,
    memory_used_mb DOUBLE PRECISION,
    disk_percent DOUBLE PRECISION,
    net_rx_mb DOUBLE PRECISION,
    net_tx_mb DOUBLE PRECISION,
    queue_depth INTEGER,
    latency_ms DOUBLE PRECISION,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE system_metrics IS 'Time-series service performance metrics for monitoring and alerting.';

-- =========================================================
-- 13) backtest_runs: Backtest summary records
-- =========================================================
CREATE TABLE IF NOT EXISTS backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    run_uuid UUID DEFAULT gen_random_uuid(),
    strategy_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    initial_capital NUMERIC(20, 8),
    final_capital NUMERIC(20, 8),
    pnl NUMERIC(20, 8),
    pnl_pct DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    sortino_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    win_rate DOUBLE PRECISION,
    trade_count INTEGER,
    config JSONB NOT NULL DEFAULT '{}'::JSONB,
    metrics JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE backtest_runs IS 'Backtest run-level summaries and performance statistics.';

-- =========================================================
-- 14) backtest_trades: Trade-level backtest events
-- =========================================================
CREATE TABLE IF NOT EXISTS backtest_trades (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity NUMERIC(30, 10) NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    entry_price NUMERIC(20, 10) NOT NULL,
    exit_price NUMERIC(20, 10),
    fee NUMERIC(20, 10) DEFAULT 0,
    pnl NUMERIC(20, 10),
    pnl_pct DOUBLE PRECISION,
    duration_seconds INTEGER,
    signal_id BIGINT,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE backtest_trades IS 'Individual simulated trades associated with a backtest run.';

-- =========================================================
-- Convert time-series tables to hypertables
-- =========================================================
SELECT create_hypertable('reddit_data', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
SELECT create_hypertable('news_data', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
SELECT create_hypertable('onchain_transactions', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
SELECT create_hypertable('price_data', 'ts', chunk_time_interval => INTERVAL '6 hours', if_not_exists => TRUE);
SELECT create_hypertable('sentiment_scores', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);
SELECT create_hypertable('sentiment_aggregates', 'ts', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
SELECT create_hypertable('technical_indicators', 'ts', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
SELECT create_hypertable('price_predictions', 'ts', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
SELECT create_hypertable('trading_signals', 'ts', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
SELECT create_hypertable('system_metrics', 'ts', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

-- =========================================================
-- Retention policies (TimescaleDB background jobs)
-- =========================================================
SELECT add_retention_policy('reddit_data', INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('news_data', INTERVAL '180 days', if_not_exists => TRUE);
SELECT add_retention_policy('onchain_transactions', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('price_data', INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('sentiment_scores', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('sentiment_aggregates', INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('technical_indicators', INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('price_predictions', INTERVAL '730 days', if_not_exists => TRUE);
SELECT add_retention_policy('trading_signals', INTERVAL '365 days', if_not_exists => TRUE);
SELECT add_retention_policy('system_metrics', INTERVAL '90 days', if_not_exists => TRUE);

-- =========================================================
-- Indexes: time, symbol/coin, composite patterns, GIN
-- =========================================================

-- reddit_data
CREATE INDEX IF NOT EXISTS idx_reddit_data_ts ON reddit_data (ts DESC);
CREATE INDEX IF NOT EXISTS idx_reddit_data_subreddit_ts ON reddit_data (subreddit, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reddit_data_source_ts ON reddit_data (source_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_reddit_data_mentioned_coins_gin ON reddit_data USING GIN (mentioned_coins);
CREATE INDEX IF NOT EXISTS idx_reddit_data_metadata_gin ON reddit_data USING GIN (metadata);

-- news_data
CREATE INDEX IF NOT EXISTS idx_news_data_ts ON news_data (ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_data_published_at ON news_data (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_data_source_ts ON news_data (source_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_news_data_mentioned_coins_gin ON news_data USING GIN (mentioned_coins);
CREATE INDEX IF NOT EXISTS idx_news_data_entities_gin ON news_data USING GIN (extracted_entities);

-- onchain_transactions
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_ts ON onchain_transactions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_symbol_ts ON onchain_transactions (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_chain_ts ON onchain_transactions (chain, ts DESC);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_flow_ts ON onchain_transactions (flow_direction, exchange_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_tags_gin ON onchain_transactions USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_metadata_gin ON onchain_transactions USING GIN (metadata);

-- price_data
CREATE INDEX IF NOT EXISTS idx_price_data_ts ON price_data (ts DESC);
CREATE INDEX IF NOT EXISTS idx_price_data_symbol_interval_ts ON price_data (symbol, interval, ts DESC);
CREATE INDEX IF NOT EXISTS idx_price_data_interval_ts ON price_data (interval, ts DESC);

-- sentiment_scores
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_ts ON sentiment_scores (ts DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_symbol_ts ON sentiment_scores (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_source_model_ts ON sentiment_scores (source_type, model_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_metadata_gin ON sentiment_scores USING GIN (metadata);

-- sentiment_aggregates
CREATE INDEX IF NOT EXISTS idx_sentiment_aggregates_ts ON sentiment_aggregates (ts DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_aggregates_window_symbol_ts ON sentiment_aggregates (window, symbol, ts DESC);

-- technical_indicators
CREATE INDEX IF NOT EXISTS idx_technical_indicators_ts ON technical_indicators (ts DESC);
CREATE INDEX IF NOT EXISTS idx_technical_indicators_symbol_interval_ts ON technical_indicators (symbol, interval, ts DESC);

-- price_predictions
CREATE INDEX IF NOT EXISTS idx_price_predictions_ts ON price_predictions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_price_predictions_symbol_horizon_ts ON price_predictions (symbol, prediction_horizon, ts DESC);
CREATE INDEX IF NOT EXISTS idx_price_predictions_model_ts ON price_predictions (model_name, model_version, ts DESC);
CREATE INDEX IF NOT EXISTS idx_price_predictions_metadata_gin ON price_predictions USING GIN (metadata);

-- trading_signals
CREATE INDEX IF NOT EXISTS idx_trading_signals_ts ON trading_signals (ts DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_symbol_interval_active_ts ON trading_signals (symbol, interval, is_active, ts DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_signal_ts ON trading_signals (signal, ts DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_factors_gin ON trading_signals USING GIN (factors);
CREATE INDEX IF NOT EXISTS idx_trading_signals_metadata_gin ON trading_signals USING GIN (metadata);

-- coin_configs
CREATE INDEX IF NOT EXISTS idx_coin_configs_is_enabled ON coin_configs (is_enabled);
CREATE INDEX IF NOT EXISTS idx_coin_configs_intervals_gin ON coin_configs USING GIN (tracked_intervals);

-- model_metadata
CREATE INDEX IF NOT EXISTS idx_model_metadata_model_active ON model_metadata (model_name, is_active);
CREATE INDEX IF NOT EXISTS idx_model_metadata_created_at ON model_metadata (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_metadata_metrics_gin ON model_metadata USING GIN (metrics);

-- system_metrics
CREATE INDEX IF NOT EXISTS idx_system_metrics_ts ON system_metrics (ts DESC);
CREATE INDEX IF NOT EXISTS idx_system_metrics_service_ts ON system_metrics (service_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_system_metrics_host_ts ON system_metrics (host, ts DESC);

-- backtest runs/trades
CREATE INDEX IF NOT EXISTS idx_backtest_runs_started_at ON backtest_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_symbol ON backtest_runs (strategy_name, symbol, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id ON backtest_trades (run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_symbol_entry_time ON backtest_trades (symbol, entry_time DESC);

-- =========================================================
-- Continuous aggregates where appropriate
-- =========================================================

-- 1h sentiment aggregate from raw sentiment_scores
CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_sentiment_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    symbol,
    COUNT(*) AS sample_count,
    AVG(sentiment_score) AS avg_sentiment,
    STDDEV_POP(sentiment_score) AS sentiment_stddev,
    AVG(CASE WHEN sentiment_score > 0.2 THEN 1.0 ELSE 0.0 END) AS bullish_ratio,
    AVG(CASE WHEN sentiment_score < -0.2 THEN 1.0 ELSE 0.0 END) AS bearish_ratio,
    AVG(CASE WHEN sentiment_score BETWEEN -0.2 AND 0.2 THEN 1.0 ELSE 0.0 END) AS neutral_ratio
FROM sentiment_scores
GROUP BY bucket, symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'cagg_sentiment_1h',
    start_offset => INTERVAL '30 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- 1h OHLCV rollup from lower interval price_data
CREATE MATERIALIZED VIEW IF NOT EXISTS cagg_price_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', ts) AS bucket,
    symbol,
    first(open, ts) AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, ts) AS close,
    sum(volume) AS volume
FROM price_data
GROUP BY bucket, symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'cagg_price_1h',
    start_offset => INTERVAL '90 days',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists => TRUE
);

-- Additional timestamp indexes for operational filtering and audits
CREATE INDEX IF NOT EXISTS idx_reddit_data_created_at ON reddit_data (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_data_created_at ON news_data (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_onchain_transactions_created_at ON onchain_transactions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_data_created_at ON price_data (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_scores_created_at ON sentiment_scores (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_aggregates_created_at ON sentiment_aggregates (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_technical_indicators_created_at ON technical_indicators (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_predictions_created_at ON price_predictions (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_price_predictions_evaluated_at ON price_predictions (evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_created_at ON trading_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trading_signals_expires_at ON trading_signals (expires_at DESC);
CREATE INDEX IF NOT EXISTS idx_coin_configs_updated_at ON coin_configs (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_metadata_trained_at ON model_metadata (trained_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_metadata_deployed_at ON model_metadata (deployed_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_metrics_created_at ON system_metrics (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_ended_at ON backtest_runs (ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_created_at ON backtest_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_entry_time ON backtest_trades (entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_exit_time ON backtest_trades (exit_time DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_created_at ON backtest_trades (created_at DESC);

-- =========================================================
-- Analytical views
-- =========================================================

-- Latest sentiment snapshot by coin
CREATE OR REPLACE VIEW v_latest_sentiment AS
SELECT DISTINCT ON (sa.symbol)
    sa.symbol,
    sa.ts,
    sa.window,
    sa.sample_count,
    sa.avg_sentiment,
    sa.sentiment_stddev,
    sa.weighted_score,
    sa.bullish_ratio,
    sa.bearish_ratio,
    sa.neutral_ratio
FROM sentiment_aggregates sa
ORDER BY sa.symbol, sa.ts DESC;

-- Currently active latest trading signals
CREATE OR REPLACE VIEW v_active_signals AS
SELECT DISTINCT ON (ts.symbol, ts.interval)
    ts.id,
    ts.ts,
    ts.symbol,
    ts.interval,
    ts.signal,
    ts.strength,
    ts.confidence,
    ts.entry_price,
    ts.stop_loss,
    ts.take_profit,
    ts.horizon_minutes,
    ts.factors,
    ts.rationale,
    ts.expires_at
FROM trading_signals ts
WHERE ts.is_active = TRUE
  AND (ts.expires_at IS NULL OR ts.expires_at > NOW())
ORDER BY ts.symbol, ts.interval, ts.ts DESC;

-- Model performance comparison by realized prediction error
CREATE OR REPLACE VIEW v_model_performance AS
SELECT
    pp.model_name,
    pp.model_version,
    COUNT(*) FILTER (WHERE pp.actual_price IS NOT NULL) AS evaluated_predictions,
    AVG(pp.error_abs) FILTER (WHERE pp.actual_price IS NOT NULL) AS mae,
    AVG(ABS(pp.error_pct)) FILTER (WHERE pp.actual_price IS NOT NULL) AS mape,
    AVG(pp.confidence) AS avg_confidence,
    MAX(pp.ts) AS last_prediction_ts,
    MAX(pp.evaluated_at) AS last_evaluation_ts
FROM price_predictions pp
GROUP BY pp.model_name, pp.model_version;
