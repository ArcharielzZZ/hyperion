-- sql/queries/aggregate-daily-stats.sql
-- Aggregates raw fills and snapshots into a daily summary for each wallet

INSERT INTO wallet_daily_stats (address, date, daily_pnl, daily_trades, daily_volume)
SELECT
    wallet_address,
    (timestamp AT TIME ZONE 'UTC')::DATE as stats_date,
    SUM(pnl) as total_pnl,
    COUNT(*) as total_trades,
    SUM(ABS(notional)) as total_volume
FROM fills
WHERE timestamp >= CURRENT_DATE - INTERVAL '1 day'
  AND timestamp < CURRENT_DATE
GROUP BY wallet_address, stats_date
ON CONFLICT (address, date) DO UPDATE SET
    daily_pnl = EXCLUDED.daily_pnl,
    daily_trades = EXCLUDED.daily_trades,
    daily_volume = EXCLUDED.daily_volume;
