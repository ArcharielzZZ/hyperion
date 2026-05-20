-- Week 1 closure: gap detector + per-channel health (see docs/WEEK1_DATA_PLATFORM.md).

CREATE OR REPLACE VIEW v_week1_data_channel_health AS
SELECT *
FROM (
    SELECT
        'fills'::TEXT AS channel,
        f.fills_max_ts AS max_ts,
        f.fills_lag_sec AS lag_sec,
        CASE
            WHEN f.fills_max_ts IS NULL THEN 'missing'
            WHEN f.fills_lag_sec > 900 THEN 'critical'
            WHEN f.fills_lag_sec > 300 THEN 'warn'
            ELSE 'ok'
        END AS status
    FROM v_data_freshness_now f
    UNION ALL
    SELECT
        'trade_ticks',
        f.trade_ticks_max_ts,
        f.trade_ticks_lag_sec,
        CASE
            WHEN f.trade_ticks_max_ts IS NULL THEN 'missing'
            WHEN f.trade_ticks_lag_sec > 900 THEN 'critical'
            WHEN f.trade_ticks_lag_sec > 300 THEN 'warn'
            ELSE 'ok'
        END
    FROM v_data_freshness_now f
    UNION ALL
    SELECT
        'positions',
        f.positions_max_ts,
        f.positions_lag_sec,
        CASE
            WHEN f.positions_max_ts IS NULL THEN 'missing'
            WHEN f.positions_lag_sec > 900 THEN 'critical'
            WHEN f.positions_lag_sec > 300 THEN 'warn'
            ELSE 'ok'
        END
    FROM v_data_freshness_now f
    UNION ALL
    SELECT
        'pnl_snapshots',
        f.pnl_snapshots_max_ts,
        f.pnl_snapshots_lag_sec,
        CASE
            WHEN f.pnl_snapshots_max_ts IS NULL THEN 'missing'
            WHEN f.pnl_snapshots_lag_sec > 900 THEN 'critical'
            WHEN f.pnl_snapshots_lag_sec > 300 THEN 'warn'
            ELSE 'ok'
        END
    FROM v_data_freshness_now f
    UNION ALL
    SELECT
        'market_snapshots',
        f.market_snapshots_max_ts,
        f.market_snapshots_lag_sec,
        CASE
            WHEN f.market_snapshots_max_ts IS NULL THEN 'missing'
            WHEN f.market_snapshots_lag_sec > 900 THEN 'critical'
            WHEN f.market_snapshots_lag_sec > 300 THEN 'warn'
            ELSE 'ok'
        END
    FROM v_data_freshness_now f
    UNION ALL
    SELECT
        'trader_scores',
        f.trader_scores_max_ts,
        f.trader_scores_lag_sec,
        CASE
            WHEN f.trader_scores_max_ts IS NULL THEN 'missing'
            WHEN f.trader_scores_lag_sec > 7200 THEN 'critical'
            WHEN f.trader_scores_lag_sec > 3900 THEN 'warn'
            ELSE 'ok'
        END
    FROM v_data_freshness_now f
) channels;

COMMENT ON VIEW v_week1_data_channel_health IS
    'Per-table freshness with ok/warn/critical/missing (Week 1 gap detector).';

-- Pair connect -> session_complete using window functions (O(n) on audit log).
CREATE OR REPLACE VIEW v_week1_ingest_sessions AS
WITH ordered AS (
    SELECT
        occurred_at,
        event_type,
        detail,
        LEAD(occurred_at) OVER (ORDER BY occurred_at, id) AS next_at,
        LEAD(event_type) OVER (ORDER BY occurred_at, id) AS next_type,
        LEAD(detail) OVER (ORDER BY occurred_at, id) AS next_detail
    FROM ingest_connection_events
    WHERE event_type IN ('ws_connected', 'ws_session_cycle_complete')
      AND occurred_at > NOW() - INTERVAL '30 days'
)
SELECT
    occurred_at AS connected_at,
    next_at AS session_ended_at,
    next_detail ->> 'outcome' AS session_outcome,
    EXTRACT(EPOCH FROM (next_at - occurred_at))::DOUBLE PRECISION AS session_duration_sec
FROM ordered
WHERE event_type = 'ws_connected'
  AND next_type = 'ws_session_cycle_complete'
ORDER BY connected_at DESC;

COMMENT ON VIEW v_week1_ingest_sessions IS
    'Completed Hyperliquid websocket sessions from audit events.';

CREATE OR REPLACE VIEW v_week1_ingest_downtime_gaps AS
WITH sessions AS (
    SELECT
        session_ended_at,
        session_outcome,
        LEAD(connected_at) OVER (ORDER BY connected_at) AS next_connected_at
    FROM v_week1_ingest_sessions
)
SELECT
    session_ended_at AS gap_start,
    next_connected_at AS gap_end,
    EXTRACT(EPOCH FROM (next_connected_at - session_ended_at))::DOUBLE PRECISION AS gap_sec,
    session_outcome AS prior_session_outcome
FROM sessions
WHERE next_connected_at IS NOT NULL
  AND next_connected_at > session_ended_at
  AND EXTRACT(EPOCH FROM (next_connected_at - session_ended_at)) > 60
ORDER BY gap_start DESC;

COMMENT ON VIEW v_week1_ingest_downtime_gaps IS
    'Reconnect gaps longer than 60 seconds (ingest was offline).';

CREATE OR REPLACE VIEW v_week1_health_summary AS
WITH gaps_24h AS (
    SELECT
        COUNT(*)::BIGINT AS ingest_gaps_24h,
        COALESCE(MAX(gap_sec), 0)::DOUBLE PRECISION AS ingest_max_gap_sec_24h
    FROM v_week1_ingest_downtime_gaps
    WHERE gap_start > NOW() - INTERVAL '24 hours'
),
channels AS (
    SELECT
        COUNT(*) FILTER (WHERE status = 'critical')::BIGINT AS channels_critical,
        COUNT(*) FILTER (WHERE status = 'warn')::BIGINT AS channels_warn,
        COUNT(*) FILTER (WHERE status = 'missing')::BIGINT AS channels_missing
    FROM v_week1_data_channel_health
)
SELECT
    f.observed_at,
    c.channels_critical,
    c.channels_warn,
    c.channels_missing,
    g.ingest_gaps_24h,
    g.ingest_max_gap_sec_24h,
    (
        SELECT MAX(occurred_at)
        FROM ingest_connection_events
        WHERE event_type = 'ws_connected'
    ) AS last_ws_connected_at,
    (
        SELECT COUNT(*)::BIGINT
        FROM ingest_connection_events
        WHERE occurred_at > NOW() - INTERVAL '24 hours'
    ) AS ingest_events_24h,
    (
        SELECT COUNT(*)::BIGINT
        FROM scanner_coverage_snapshots
        WHERE captured_at > NOW() - INTERVAL '24 hours'
    ) AS scanner_snapshots_24h,
    (SELECT MAX(snapshot_date) FROM discovery_universe_daily) AS latest_discovery_snapshot_date
FROM v_data_freshness_now f
CROSS JOIN channels c
CROSS JOIN gaps_24h g;

COMMENT ON VIEW v_week1_health_summary IS
    'Week 1 closure rollup: channel lag, ingest gaps, snapshot cadence.';
