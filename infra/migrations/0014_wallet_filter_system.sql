-- Wallet filter system (SUMMARY_OPTIMIERT): status/label on traders + daily/multi-window stats.

ALTER TABLE traders
    ADD COLUMN IF NOT EXISTS wallet_status TEXT NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS wallet_label TEXT NOT NULL DEFAULT 'NEW_WALLET',
    ADD COLUMN IF NOT EXISTS wallet_filter_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS behavior_tier TEXT,
    ADD COLUMN IF NOT EXISTS filter_discovery_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS filter_avg_confidence DOUBLE PRECISION;

COMMENT ON COLUMN traders.wallet_status IS 'PENDING | PROMOTED | BANNED | ARCHIVED';
COMMENT ON COLUMN traders.wallet_label IS 'NEW_WALLET | NORMAL | HIGH_FREQUENCY | ULTRA_HF_BOT';

CREATE TABLE IF NOT EXISTS wallet_daily_stats (
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE,
    stats_date DATE NOT NULL,
    daily_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
    daily_trades INTEGER NOT NULL DEFAULT 0,
    daily_volume DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (trader_id, stats_date)
);

CREATE TABLE IF NOT EXISTS wallet_performance (
    trader_id UUID PRIMARY KEY REFERENCES traders(id) ON DELETE CASCADE,
    pnl_7d DOUBLE PRECISION NOT NULL DEFAULT 0,
    trades_7d INTEGER NOT NULL DEFAULT 0,
    pnl_1m DOUBLE PRECISION NOT NULL DEFAULT 0,
    trades_1m INTEGER NOT NULL DEFAULT 0,
    pnl_3m DOUBLE PRECISION NOT NULL DEFAULT 0,
    trades_3m INTEGER NOT NULL DEFAULT 0,
    pnl_6m DOUBLE PRECISION NOT NULL DEFAULT 0,
    trades_6m INTEGER NOT NULL DEFAULT 0,
    total_volume DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_trades INTEGER NOT NULL DEFAULT 0,
    last_trade_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wallet_daily_stats_date ON wallet_daily_stats(stats_date);
CREATE INDEX IF NOT EXISTS idx_traders_wallet_status ON traders(wallet_status);
