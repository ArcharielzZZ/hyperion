ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS quality_weight DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS momentum_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS volatility_score DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS market_context TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT,
    ADD COLUMN IF NOT EXISTS consensus_window_key TEXT,
    ADD COLUMN IF NOT EXISTS signal_window_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS signal_window_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS reference_price DOUBLE PRECISION;

UPDATE signals
SET confidence_score = COALESCE(confidence_score, score),
    quality_weight = COALESCE(quality_weight, supporting_traders),
    momentum_score = COALESCE(momentum_score, 50.0),
    volatility_score = COALESCE(volatility_score, 50.0),
    market_context = COALESCE(market_context, '{}'),
    status = COALESCE(status, 'resolved'),
    consensus_window_key = COALESCE(consensus_window_key, id::TEXT),
    signal_window_start = COALESCE(signal_window_start, timestamp),
    signal_window_end = COALESCE(signal_window_end, timestamp),
    activated_at = COALESCE(activated_at, timestamp),
    expires_at = COALESCE(expires_at, timestamp + INTERVAL '4 hours'),
    resolved_at = COALESCE(resolved_at, timestamp),
    reference_price = COALESCE(reference_price, 0.0);

ALTER TABLE signals
    ALTER COLUMN confidence_score SET NOT NULL,
    ALTER COLUMN quality_weight SET NOT NULL,
    ALTER COLUMN momentum_score SET NOT NULL,
    ALTER COLUMN volatility_score SET NOT NULL,
    ALTER COLUMN market_context SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN consensus_window_key SET NOT NULL,
    ALTER COLUMN signal_window_start SET NOT NULL,
    ALTER COLUMN signal_window_end SET NOT NULL,
    ALTER COLUMN activated_at SET NOT NULL,
    ALTER COLUMN expires_at SET NOT NULL,
    ALTER COLUMN reference_price SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_consensus_window_key
    ON signals(consensus_window_key);
CREATE INDEX IF NOT EXISTS idx_signals_status_timestamp
    ON signals(status, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_confidence_score
    ON signals(confidence_score DESC, timestamp DESC);

CREATE TABLE IF NOT EXISTS signal_contributors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE,
    wallet TEXT NOT NULL,
    trader_score DOUBLE PRECISION NOT NULL,
    weight DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    entry_timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(signal_id, trader_id)
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id UUID NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    horizon_minutes INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price DOUBLE PRECISION NOT NULL,
    realized_return_bps DOUBLE PRECISION NOT NULL,
    max_favorable_excursion_bps DOUBLE PRECISION NOT NULL,
    max_adverse_excursion_bps DOUBLE PRECISION NOT NULL,
    outcome_label TEXT NOT NULL,
    resolved_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(signal_id, horizon_minutes)
);

CREATE INDEX IF NOT EXISTS idx_signal_contributors_signal
    ON signal_contributors(signal_id, trader_score DESC);
CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal
    ON signal_outcomes(signal_id, horizon_minutes);
