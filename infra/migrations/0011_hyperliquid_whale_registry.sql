-- Curated Hyperliquid whales (leaderboard + hit-rate discovery). Survives trader-engine refresh.
CREATE TABLE IF NOT EXISTS hyperliquid_whale_registry (
    wallet TEXT PRIMARY KEY,
    all_time_vlm_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    all_time_roi DOUBLE PRECISION NOT NULL DEFAULT 0,
    hit_rate_pct DOUBLE PRECISION,
    whale_tier TEXT NOT NULL DEFAULT 'whale',
    source TEXT NOT NULL DEFAULT 'leaderboard_discovery',
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_hyperliquid_whale_registry_tier
    ON hyperliquid_whale_registry (whale_tier, all_time_vlm_usd DESC);

COMMENT ON TABLE hyperliquid_whale_registry IS
    'Curated whale wallets; trader-engine forces promoted + rank_tier whale for these addresses.';
