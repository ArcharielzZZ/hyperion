ALTER TABLE fills ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS snapshot_key TEXT;
ALTER TABLE pnl_snapshots ADD COLUMN IF NOT EXISTS snapshot_key TEXT;
ALTER TABLE market_snapshots ADD COLUMN IF NOT EXISTS snapshot_key TEXT;

UPDATE fills
SET event_key = COALESCE(event_key, coin || ':' || side || ':' || price || ':' || size || ':' || timestamp)
WHERE event_key IS NULL;

UPDATE positions
SET snapshot_key = COALESCE(snapshot_key, trader_id::TEXT || ':' || coin || ':' || timestamp)
WHERE snapshot_key IS NULL;

UPDATE pnl_snapshots
SET snapshot_key = COALESCE(snapshot_key, trader_id::TEXT || ':equity:' || timestamp)
WHERE snapshot_key IS NULL;

UPDATE market_snapshots
SET snapshot_key = COALESCE(snapshot_key, coin || ':market:' || timestamp)
WHERE snapshot_key IS NULL;

ALTER TABLE fills ALTER COLUMN event_key SET NOT NULL;
ALTER TABLE positions ALTER COLUMN snapshot_key SET NOT NULL;
ALTER TABLE pnl_snapshots ALTER COLUMN snapshot_key SET NOT NULL;
ALTER TABLE market_snapshots ALTER COLUMN snapshot_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_event_key ON fills(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_snapshot_key ON positions(snapshot_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pnl_snapshots_snapshot_key ON pnl_snapshots(snapshot_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_snapshots_snapshot_key ON market_snapshots(snapshot_key);

CREATE TABLE IF NOT EXISTS trade_ticks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    trade_hash TEXT NOT NULL,
    trade_id BIGINT NOT NULL,
    buyer_wallet TEXT,
    seller_wallet TEXT,
    timestamp TIMESTAMPTZ NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS liquidation_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id UUID REFERENCES traders(id) ON DELETE SET NULL,
    wallet TEXT NOT NULL,
    coin TEXT NOT NULL,
    side TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
