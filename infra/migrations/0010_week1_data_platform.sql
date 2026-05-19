-- Week 1 quant roadmap: observability, cohort versioning, storage hints.
-- See docs/WEEK1_DATA_PLATFORM.md for operations and definition-of-done.

-- 1) Ingest reconnect / lifecycle audit (append-only)
CREATE TABLE IF NOT EXISTS ingest_connection_events (
    id BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event_type TEXT NOT NULL,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_ingest_connection_events_time
    ON ingest_connection_events (occurred_at DESC);

COMMENT ON TABLE ingest_connection_events IS
    'Hyperliquid websocket lifecycle: connects, failures, timeouts, backoff reconnects.';

-- 2) Daily discovery universe snapshot (as-of research cohort)
CREATE TABLE IF NOT EXISTS discovery_universe_daily (
    snapshot_date DATE NOT NULL,
    trader_id UUID NOT NULL REFERENCES traders (id) ON DELETE CASCADE,
    wallet TEXT NOT NULL,
    promoted BOOLEAN NOT NULL,
    discovery_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    rank_tier TEXT NOT NULL DEFAULT '',
    activity_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    data_coverage_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    latest_behavior_score DOUBLE PRECISION NOT NULL DEFAULT 0,
    fills_24h INTEGER NOT NULL DEFAULT 0,
    positions_24h INTEGER NOT NULL DEFAULT 0,
    pnl_snapshots_24h INTEGER NOT NULL DEFAULT 0,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (snapshot_date, trader_id)
);

CREATE INDEX IF NOT EXISTS idx_discovery_universe_daily_date_wallet
    ON discovery_universe_daily (snapshot_date DESC, wallet);

COMMENT ON TABLE discovery_universe_daily IS
    'Immutable daily copy of trader_discovery_rankings rows for reproducible research.';

-- 3) Point-in-time scanner / freshness payloads (JSON for forward-compatible fields)
CREATE TABLE IF NOT EXISTS scanner_coverage_snapshots (
    id BIGSERIAL PRIMARY KEY,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scanner_coverage_snapshots_time
    ON scanner_coverage_snapshots (captured_at DESC);

COMMENT ON TABLE scanner_coverage_snapshots IS
    'Scheduled captures of cross-table freshness, counts, and lag metrics.';

-- 4) Live freshness view (no history; cheap SELECT for APIs / ad hoc checks)
CREATE OR REPLACE VIEW v_data_freshness_now AS
SELECT
    NOW() AS observed_at,
    (SELECT COUNT(*)::BIGINT FROM traders) AS traders_total,
    (SELECT COUNT(*)::BIGINT FROM traders WHERE last_seen > NOW() - INTERVAL '30 days') AS traders_active_30d,
    (SELECT COUNT(*)::BIGINT FROM fills) AS fills_total,
    (SELECT MAX(timestamp) FROM fills) AS fills_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM fills)))::DOUBLE PRECISION AS fills_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM trade_ticks) AS trade_ticks_total,
    (SELECT MAX(timestamp) FROM trade_ticks) AS trade_ticks_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM trade_ticks)))::DOUBLE PRECISION AS trade_ticks_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM positions) AS positions_total,
    (SELECT MAX(timestamp) FROM positions) AS positions_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM positions)))::DOUBLE PRECISION AS positions_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM pnl_snapshots) AS pnl_snapshots_total,
    (SELECT MAX(timestamp) FROM pnl_snapshots) AS pnl_snapshots_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM pnl_snapshots)))::DOUBLE PRECISION AS pnl_snapshots_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM market_snapshots) AS market_snapshots_total,
    (SELECT MAX(timestamp) FROM market_snapshots) AS market_snapshots_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM market_snapshots)))::DOUBLE PRECISION AS market_snapshots_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM trader_scores) AS trader_scores_rows,
    (SELECT MAX(timestamp) FROM trader_scores) AS trader_scores_max_ts,
    EXTRACT(EPOCH FROM (NOW() - (SELECT MAX(timestamp) FROM trader_scores)))::DOUBLE PRECISION AS trader_scores_lag_sec,
    (SELECT COUNT(*)::BIGINT FROM trader_discovery_rankings WHERE promoted = TRUE) AS discovery_promoted;

COMMENT ON VIEW v_data_freshness_now IS
    'Single-row snapshot of max timestamps and wall-clock lag for hot tables.';

-- 5) Time-ordered scan efficiency (BRIN on large append-mostly tables)
CREATE INDEX IF NOT EXISTS brin_fills_timestamp ON fills USING BRIN (timestamp);
CREATE INDEX IF NOT EXISTS brin_trade_ticks_timestamp ON trade_ticks USING BRIN (timestamp);
CREATE INDEX IF NOT EXISTS brin_market_snapshots_timestamp ON market_snapshots USING BRIN (timestamp);
