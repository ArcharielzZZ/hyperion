CREATE TABLE IF NOT EXISTS simulation_batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    dataset_start TIMESTAMPTZ NOT NULL,
    dataset_end TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE simulation_runs
    ADD COLUMN IF NOT EXISTS batch_id UUID REFERENCES simulation_batches(id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS strategy_key TEXT,
    ADD COLUMN IF NOT EXISTS dataset_start TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS dataset_end TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS trade_count INTEGER,
    ADD COLUMN IF NOT EXISTS sharpe_like DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS expectancy DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS profit_factor DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS avg_trade_return_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS config_json TEXT;

UPDATE simulation_runs
SET strategy_key = COALESCE(strategy_key, strategy_name),
    dataset_start = COALESCE(dataset_start, created_at),
    dataset_end = COALESCE(dataset_end, created_at),
    trade_count = COALESCE(trade_count, 0),
    sharpe_like = COALESCE(sharpe_like, 0.0),
    expectancy = COALESCE(expectancy, 0.0),
    profit_factor = COALESCE(profit_factor, 0.0),
    avg_trade_return_pct = COALESCE(avg_trade_return_pct, 0.0),
    config_json = COALESCE(config_json, '{}');

ALTER TABLE simulation_runs
    ALTER COLUMN strategy_key SET NOT NULL,
    ALTER COLUMN dataset_start SET NOT NULL,
    ALTER COLUMN dataset_end SET NOT NULL,
    ALTER COLUMN trade_count SET NOT NULL,
    ALTER COLUMN sharpe_like SET NOT NULL,
    ALTER COLUMN expectancy SET NOT NULL,
    ALTER COLUMN profit_factor SET NOT NULL,
    ALTER COLUMN avg_trade_return_pct SET NOT NULL,
    ALTER COLUMN config_json SET NOT NULL;

CREATE TABLE IF NOT EXISTS simulation_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL UNIQUE REFERENCES simulation_runs(id) ON DELETE CASCADE,
    total_fees_paid DOUBLE PRECISION NOT NULL,
    total_slippage_paid DOUBLE PRECISION NOT NULL,
    avg_win_pct DOUBLE PRECISION NOT NULL,
    avg_loss_pct DOUBLE PRECISION NOT NULL,
    payoff_ratio DOUBLE PRECISION NOT NULL,
    return_volatility_pct DOUBLE PRECISION NOT NULL,
    peak_equity DOUBLE PRECISION NOT NULL,
    trough_equity DOUBLE PRECISION NOT NULL,
    max_consecutive_wins INTEGER NOT NULL,
    max_consecutive_losses INTEGER NOT NULL,
    drawdown_curve_json TEXT NOT NULL,
    regime_breakdown_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS strategy_key TEXT,
    ADD COLUMN IF NOT EXISTS entry_reason TEXT,
    ADD COLUMN IF NOT EXISTS exit_reason TEXT,
    ADD COLUMN IF NOT EXISTS regime TEXT,
    ADD COLUMN IF NOT EXISTS entry_fee_paid DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exit_fee_paid DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS entry_slippage_bps DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS exit_slippage_bps DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS return_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_favorable_excursion_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_adverse_excursion_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS equity_before DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS equity_after DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS risk_fraction DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS holding_minutes INTEGER,
    ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION;

UPDATE paper_trades pt
SET strategy_key = COALESCE(pt.strategy_key, sr.strategy_key, sr.strategy_name),
    entry_reason = COALESCE(pt.entry_reason, 'signal_entry'),
    exit_reason = COALESCE(pt.exit_reason, 'time_exit'),
    regime = COALESCE(pt.regime, 'unknown'),
    entry_fee_paid = COALESCE(pt.entry_fee_paid, pt.fee_paid / 2.0),
    exit_fee_paid = COALESCE(pt.exit_fee_paid, pt.fee_paid / 2.0),
    entry_slippage_bps = COALESCE(pt.entry_slippage_bps, 0.0),
    exit_slippage_bps = COALESCE(pt.exit_slippage_bps, 0.0),
    return_pct = COALESCE(
        pt.return_pct,
        CASE
            WHEN pt.entry_price > 0
            THEN ((pt.exit_price - pt.entry_price) / pt.entry_price) * 100.0
            ELSE 0.0
        END
    ),
    max_favorable_excursion_pct = COALESCE(pt.max_favorable_excursion_pct, 0.0),
    max_adverse_excursion_pct = COALESCE(pt.max_adverse_excursion_pct, 0.0),
    equity_before = COALESCE(pt.equity_before, 0.0),
    equity_after = COALESCE(pt.equity_after, pt.pnl),
    risk_fraction = COALESCE(pt.risk_fraction, 0.0),
    holding_minutes = COALESCE(
        pt.holding_minutes,
        GREATEST(
            0,
            FLOOR(EXTRACT(EPOCH FROM (pt.closed_at - pt.opened_at)) / 60.0)::INTEGER
        )
    ),
    confidence_score = COALESCE(pt.confidence_score, 0.0)
FROM simulation_runs sr
WHERE pt.run_id = sr.id;

ALTER TABLE paper_trades
    ALTER COLUMN strategy_key SET NOT NULL,
    ALTER COLUMN entry_reason SET NOT NULL,
    ALTER COLUMN exit_reason SET NOT NULL,
    ALTER COLUMN regime SET NOT NULL,
    ALTER COLUMN entry_fee_paid SET NOT NULL,
    ALTER COLUMN exit_fee_paid SET NOT NULL,
    ALTER COLUMN entry_slippage_bps SET NOT NULL,
    ALTER COLUMN exit_slippage_bps SET NOT NULL,
    ALTER COLUMN return_pct SET NOT NULL,
    ALTER COLUMN max_favorable_excursion_pct SET NOT NULL,
    ALTER COLUMN max_adverse_excursion_pct SET NOT NULL,
    ALTER COLUMN equity_before SET NOT NULL,
    ALTER COLUMN equity_after SET NOT NULL,
    ALTER COLUMN risk_fraction SET NOT NULL,
    ALTER COLUMN holding_minutes SET NOT NULL,
    ALTER COLUMN confidence_score SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_simulation_batches_created_at
    ON simulation_batches(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_batch_id
    ON simulation_runs(batch_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_runs_strategy_key
    ON simulation_runs(strategy_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_simulation_reports_run_id
    ON simulation_reports(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_run_id_opened_at
    ON paper_trades(run_id, opened_at ASC);
CREATE INDEX IF NOT EXISTS idx_paper_trades_signal_id
    ON paper_trades(signal_id);
