CREATE TABLE IF NOT EXISTS trader_behavior_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE,
    rolling_consistency_score_7d DOUBLE PRECISION NOT NULL,
    rolling_consistency_score_30d DOUBLE PRECISION NOT NULL,
    avg_daily_pnl_7d DOUBLE PRECISION NOT NULL,
    avg_daily_pnl_30d DOUBLE PRECISION NOT NULL,
    pnl_volatility_7d DOUBLE PRECISION NOT NULL,
    pnl_volatility_30d DOUBLE PRECISION NOT NULL,
    win_rate_30d DOUBLE PRECISION NOT NULL,
    recent_leverage_avg_7d DOUBLE PRECISION NOT NULL,
    leverage_avg_30d DOUBLE PRECISION NOT NULL,
    leverage_peak_30d DOUBLE PRECISION NOT NULL,
    leverage_volatility_30d DOUBLE PRECISION NOT NULL,
    max_drawdown_pct_30d DOUBLE PRECISION NOT NULL,
    avg_hold_duration_secs_30d DOUBLE PRECISION NOT NULL,
    median_hold_duration_secs_30d DOUBLE PRECISION NOT NULL,
    entry_timing_edge_bps_30d DOUBLE PRECISION NOT NULL,
    favorable_entry_rate_30d DOUBLE PRECISION NOT NULL,
    behavioral_drift_score DOUBLE PRECISION NOT NULL,
    emotional_volatility_score DOUBLE PRECISION NOT NULL,
    revenge_trading_score DOUBLE PRECISION NOT NULL,
    sizing_instability_score DOUBLE PRECISION NOT NULL,
    consistency_score_delta DOUBLE PRECISION NOT NULL,
    active_days_30d INTEGER NOT NULL,
    fills_7d INTEGER NOT NULL,
    fills_30d INTEGER NOT NULL,
    lifecycle_stage TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS behavioral_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trader_id UUID NOT NULL REFERENCES traders(id) ON DELETE CASCADE,
    alert_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    threshold_value DOUBLE PRECISION NOT NULL,
    detected_at TIMESTAMPTZ NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_behavior_profiles_trader_time
    ON trader_behavior_profiles(trader_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_behavior_alerts_trader_time
    ON behavioral_alerts(trader_id, detected_at DESC);
