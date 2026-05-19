-- Tables owned by the Python hyperion_pipeline package (backfill, analytics snapshots).
-- Core trading tables (traders, fills, ...) remain the source of truth for live Rust ingest.

CREATE TABLE IF NOT EXISTS wallet_sync_state (
    wallet_address TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'hyperliquid',
    sync_status TEXT NOT NULL DEFAULT 'idle',
    cursor_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_watermark TIMESTAMPTZ,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wallet_sync_state_status ON wallet_sync_state(sync_status, updated_at DESC);

CREATE TABLE IF NOT EXISTS backfill_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address TEXT NOT NULL,
    job_id UUID NOT NULL,
    chunk_index INTEGER NOT NULL,
    range_start TIMESTAMPTZ NOT NULL,
    range_end TIMESTAMPTZ NOT NULL,
    rows_processed BIGINT NOT NULL DEFAULT 0,
    parquet_paths TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_backfill_checkpoints_wallet ON backfill_checkpoints(wallet_address, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backfill_checkpoints_job ON backfill_checkpoints(job_id, chunk_index);

CREATE TABLE IF NOT EXISTS pipeline_wallet_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    window_seconds BIGINT NOT NULL,
    metrics JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (wallet_address, as_of, window_seconds)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_wallet_metrics_wallet ON pipeline_wallet_metrics(wallet_address, as_of DESC);

CREATE TABLE IF NOT EXISTS pipeline_ranking_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    formula_version TEXT NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    rankings JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_ranking_snapshots_as_of ON pipeline_ranking_snapshots(as_of DESC);

CREATE TABLE IF NOT EXISTS pipeline_funding_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_address TEXT NOT NULL,
    coin TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    event_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_funding_wallet_time ON pipeline_funding_payments(wallet_address, timestamp DESC);
