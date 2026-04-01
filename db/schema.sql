CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS market_ticks (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    volume DOUBLE PRECISION DEFAULT 0
);

SELECT create_hypertable('market_ticks', 'ts', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS trading_signals (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    signal TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_trading_signals_symbol_ts
ON trading_signals (symbol, ts DESC);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id BIGSERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    sharpe DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    pnl DOUBLE PRECISION,
    report JSONB DEFAULT '{}'::jsonb
);
