CREATE TABLE IF NOT EXISTS trader_discovery_rankings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE UNIQUE,
    wallet TEXT NOT NULL,
    public_trade_count_1h INTEGER NOT NULL,
    public_trade_count_24h INTEGER NOT NULL,
    public_notional_usd_1h DOUBLE PRECISION NOT NULL,
    public_notional_usd_24h DOUBLE PRECISION NOT NULL,
    active_coins_24h INTEGER NOT NULL,
    buy_ratio_24h DOUBLE PRECISION NOT NULL,
    fills_24h INTEGER NOT NULL,
    positions_24h INTEGER NOT NULL,
    pnl_snapshots_24h INTEGER NOT NULL,
    last_public_trade_at TIMESTAMPTZ,
    latest_behavior_score DOUBLE PRECISION NOT NULL,
    data_coverage_score DOUBLE PRECISION NOT NULL,
    activity_score DOUBLE PRECISION NOT NULL,
    discovery_score DOUBLE PRECISION NOT NULL,
    rank_tier TEXT NOT NULL,
    promoted BOOLEAN NOT NULL DEFAULT FALSE,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trader_discovery_rankings_score
    ON trader_discovery_rankings(discovery_score DESC, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_trader_discovery_rankings_promoted
    ON trader_discovery_rankings(promoted, discovery_score DESC);

CREATE INDEX IF NOT EXISTS idx_trader_discovery_rankings_last_trade
    ON trader_discovery_rankings(last_public_trade_at DESC);
