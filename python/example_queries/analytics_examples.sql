-- Example: pipeline-owned metrics (JSONB) — filter by wallet and window.
SELECT wallet_address, as_of, window_seconds, metrics
FROM pipeline_wallet_metrics
WHERE wallet_address = lower('0x...')
ORDER BY as_of DESC
LIMIT 20;

-- Example: latest ranking snapshot (JSON payload from Python RankingEngine).
SELECT id, formula_version, as_of, jsonb_array_length(rankings->'entries') AS n_entries
FROM pipeline_ranking_snapshots
ORDER BY as_of DESC
LIMIT 5;

-- Example: join Rust fills with pipeline sync state (operational view).
SELECT w.wallet_address, w.sync_status, w.last_watermark, w.updated_at
FROM wallet_sync_state w
ORDER BY w.updated_at DESC
LIMIT 50;
