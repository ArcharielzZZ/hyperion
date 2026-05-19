-- Enrichment persisted from Hyperliquid userFills payload (exchange-reported realization + fees).
-- Nullable for historical rows inserted before this migration.

ALTER TABLE fills
    ADD COLUMN IF NOT EXISTS fill_dir TEXT;

ALTER TABLE fills
    ADD COLUMN IF NOT EXISTS closed_pnl_usd DOUBLE PRECISION;

ALTER TABLE fills
    ADD COLUMN IF NOT EXISTS fee_usd DOUBLE PRECISION;

COMMENT ON COLUMN fills.fill_dir IS
    'Hyperliquid ''dir'' (e.g. Open Long, Close Long). NULL for legacy inserts.';

COMMENT ON COLUMN fills.closed_pnl_usd IS
    'Exchange closedPnl (USDC) for this slice; typically non-zero only on closes.';

COMMENT ON COLUMN fills.fee_usd IS
    'Exchange fee converted to numeric USDC magnitude (see ingest parser).';
