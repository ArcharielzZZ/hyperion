-- Advisory-only copy metrics (never used for promotion thresholds in trader-engine).
CREATE TABLE IF NOT EXISTS trader_copyability_advisory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE,
    wallet TEXT NOT NULL,
    copyability_score DOUBLE PRECISION NOT NULL CHECK (copyability_score >= 0 AND copyability_score <= 100),
    signal_frequency_component DOUBLE PRECISION NOT NULL,
    timing_component DOUBLE PRECISION NOT NULL,
    realized_ratio_component DOUBLE PRECISION NOT NULL,
    stability_component DOUBLE PRECISION NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    horizon_days DOUBLE PRECISION NOT NULL DEFAULT 30
);

CREATE INDEX IF NOT EXISTS idx_copyability_wallet_time
    ON trader_copyability_advisory (wallet, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_copyability_trader_time
    ON trader_copyability_advisory (trader_id, computed_at DESC);

COMMENT ON TABLE trader_copyability_advisory IS
    'Research-only copy suitability score (0–100). Not consulted by trader-engine promotion.';
