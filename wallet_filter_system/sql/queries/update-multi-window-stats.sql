-- sql/queries/update-multi-window-stats.sql
-- Aggregates daily statistics into multi-window performance metrics (7d, 1m, 3m, 6m)

INSERT INTO wallet_performance (
    address,
    pnl_7d, trades_7d, roi_7d,
    pnl_1m, trades_1m, roi_1m,
    pnl_3m, trades_3m, roi_3m,
    pnl_6m, trades_6m, roi_6m,
    updated_at
)
SELECT
    address,
    -- 7 Days
    COALESCE(SUM(daily_pnl) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '7 days'), 0) as pnl_7d,
    COALESCE(SUM(daily_trades) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '7 days'), 0) as trades_7d,
    -- ROI calculation: (PnL / Max Capital) - simplified placeholder
    0 as roi_7d, 

    -- 1 Month (30 Days)
    COALESCE(SUM(daily_pnl) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '30 days'), 0) as pnl_1m,
    COALESCE(SUM(daily_trades) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '30 days'), 0) as trades_1m,
    0 as roi_1m,

    -- 3 Months (90 Days)
    COALESCE(SUM(daily_pnl) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '90 days'), 0) as pnl_3m,
    COALESCE(SUM(daily_trades) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '90 days'), 0) as trades_3m,
    0 as roi_3m,

    -- 6 Months (180 Days)
    COALESCE(SUM(daily_pnl) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '180 days'), 0) as pnl_6m,
    COALESCE(SUM(daily_trades) FILTER (WHERE date >= CURRENT_DATE - INTERVAL '180 days'), 0) as trades_6m,
    0 as roi_6m,

    NOW()
FROM wallet_daily_stats
GROUP BY address
ON CONFLICT (address) DO UPDATE SET
    pnl_7d = EXCLUDED.pnl_7d,
    trades_7d = EXCLUDED.trades_7d,
    roi_7d = EXCLUDED.roi_7d,
    pnl_1m = EXCLUDED.pnl_1m,
    trades_1m = EXCLUDED.trades_1m,
    roi_1m = EXCLUDED.roi_1m,
    pnl_3m = EXCLUDED.pnl_3m,
    trades_3m = EXCLUDED.trades_3m,
    roi_3m = EXCLUDED.roi_3m,
    pnl_6m = EXCLUDED.pnl_6m,
    trades_6m = EXCLUDED.trades_6m,
    roi_6m = EXCLUDED.roi_6m,
    updated_at = EXCLUDED.updated_at;
