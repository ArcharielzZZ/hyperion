-- =============================================================================
-- COPY TRADE — ROUND TRIP COMPLETENESS AUDIT (POSTGRES)
-- -----------------------------------------------------------------------------
-- Prerequisites: migrations 0012+ (fills.fill_dir, closed_pnl_usd, fee_usd).
-- Adjustable window in params CTE (default 30d lookback).
--
-- Semantics:
-- a) Total fill events in-window for promoted wallets.
-- b) Exchange-labeled CLOSE fills (`fill_dir` ILIKE 'Close%'); each counts as ONE
--    closed-leg event attributable to HL. **Gotcha:** this is vendor-classified
--    closes, NOT a reconstructed FIFO accountant.
-- c) Approximate orphaned opens when position still open **at window end**: any
--    coin with |cum_signed_position| ≥ ε after processing all in-window fills.
-- d) Orphan exchange closes attempted from ~flat (~|cum_signed_before|<ε on coin).
-- e) Σ(exchange CLOSED rows: COALESCE(closed_pnl_usd,0)-COALESCE(fee_usd,0)).
--    **Gotcha:** if Hyperliquid convention already nets fee inside closed_pnl,
--    subtracting fee double-counts; validate against UI for sample wallets.
-- f) Ratio: (e) / Σ|px×sz| on those same CLOSE rows only.
--
-- GATE: insufficient_evidence iff (exchange_close_fill_count_in_window < 20)
-- -------------------------------------------------------------------------------

WITH params AS (
    /* Change interval here OR wrap in INSERT…SELECT replacing NOW() anchors. */
    SELECT
        CURRENT_TIMESTAMP AT TIME ZONE 'utc' AS ts_anchor,
        CURRENT_TIMESTAMP AT TIME ZONE 'utc' - INTERVAL '30 days' AS ts_start_w
),

promoted_latest AS (
    /* One row per promoted trader — latest trader_discovery_rankings row.
       JOIN condition: FK trader_id aligns fills.trader_id. */
    SELECT DISTINCT ON (dr.trader_id)
        dr.trader_id,
        t.wallet
    FROM trader_discovery_rankings dr
    INNER JOIN traders t ON t.id = dr.trader_id
    WHERE dr.promoted IS TRUE
    ORDER BY dr.trader_id, dr.timestamp DESC
),

fills_window AS (
    SELECT
        f.id,
        pl.wallet,
        f.trader_id,
        f.coin,
        f.side,
        f.size AS sz,
        f.price AS px,
        f.timestamp,
        f.event_key,
        f.fill_dir,
        f.closed_pnl_usd,
        f.fee_usd,
        CASE
            WHEN f.side ILIKE 'buy%' OR f.side ILIKE '%buy' OR upper(trim(f.side)) = 'B' THEN f.size
            WHEN f.side ILIKE 'sell%' OR f.side ILIKE '%sell' OR upper(trim(f.side)) = 'S' THEN -f.size
            WHEN f.side ILIKE 'buy' THEN f.size
            WHEN f.side ILIKE 'sell' THEN -f.size
            ELSE (
                CASE
                    WHEN f.side ILIKE 'a%' THEN -f.size
                    ELSE f.size
                END
            )
        END AS signed_coin_delta
    FROM fills f
    INNER JOIN promoted_latest pl ON pl.trader_id = f.trader_id
    CROSS JOIN params pm
    WHERE f.timestamp >= pm.ts_start_w
      AND f.timestamp <= pm.ts_anchor
),

fills_math AS (
    SELECT
        fw.*,
        SUM(signed_coin_delta) OVER (
            PARTITION BY trader_id, coin
            ORDER BY fw.timestamp ASC, fw.event_key ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS cum_coin_after_fill,
        LAG(fw.timestamp, 1) OVER (
            PARTITION BY trader_id, coin
            ORDER BY fw.timestamp ASC, fw.event_key ASC
        ) AS prev_coin_ts,
        ROW_NUMBER() OVER (
            PARTITION BY trader_id, coin
            ORDER BY fw.timestamp DESC, fw.event_key DESC
        ) AS coin_row_desc
    FROM fills_window fw
),

fills_derived AS (
    SELECT
        fm.*,
        fm.cum_coin_after_fill - fm.signed_coin_delta AS cum_coin_before_fill,
        COALESCE(fm.fill_dir, '') ILIKE 'close%' AS is_exchange_close
    FROM fills_math fm
),

wallet_facts AS (
    SELECT
        d.wallet,
        d.trader_id,
        COUNT(*)::BIGINT AS total_fill_events,

        COUNT(*) FILTER (WHERE is_exchange_close)::BIGINT AS exchange_close_fill_count,

        SUM(
            CASE
                WHEN is_exchange_close THEN COALESCE(d.closed_pnl_usd, 0.0) - COALESCE(d.fee_usd, 0.0)
                ELSE 0.0
            END
        )::DOUBLE PRECISION AS realized_net_on_exchange_closes,

        SUM(CASE WHEN is_exchange_close THEN ABS(d.sz * d.px) ELSE 0.0 END)::DOUBLE PRECISION AS close_leg_abs_notional
    FROM fills_derived d
    GROUP BY d.wallet, d.trader_id
),

orphan_positions AS (
    /* (c): rows are last in-window cumulative state per trader+coin. */
    SELECT
        d.wallet,
        d.trader_id,
        d.coin,
        d.cum_coin_after_fill
    FROM fills_derived d
    WHERE d.coin_row_desc = 1
      AND ABS(d.cum_coin_after_fill) > 1e-8
),

orphan_positions_agg AS (
    SELECT
        o.wallet,
        o.trader_id,
        COUNT(*)::BIGINT AS orphaned_open_coin_legs_window_end
    FROM orphan_positions o
    GROUP BY o.wallet, o.trader_id
),

orphan_closes_agg AS (
    /* (d): exchange says Close while coin leg was essentially flat BEFORE fill. */
    SELECT
        d.wallet,
        d.trader_id,
        COUNT(*)::BIGINT AS orphaned_close_from_flat
    FROM fills_derived d
    WHERE d.is_exchange_close
      AND ABS(d.cum_coin_before_fill) < 1e-8
    GROUP BY d.wallet, d.trader_id
),

missing_enrichment AS (
    SELECT
        d.wallet,
        d.trader_id,
        SUM(
            CASE
                WHEN d.is_exchange_close AND d.closed_pnl_usd IS NULL AND d.fee_usd IS NULL THEN 1
                ELSE 0
            END
        )::BIGINT AS close_rows_missing_pnl_fee
    FROM fills_derived d
    GROUP BY d.wallet, d.trader_id
)

SELECT
    wf.wallet,
    wf.trader_id,
    wf.total_fill_events,

    wf.exchange_close_fill_count AS fully_closed_round_trips_via_exchange_close_fill,

    COALESCE(pa.orphaned_open_coin_legs_window_end, 0) AS orphaned_opens_approx,

    COALESCE(oc.orphaned_close_from_flat, 0) AS orphaned_closes_approx,

    wf.realized_net_on_exchange_closes AS realized_net_pnl_estimate,

    wf.close_leg_abs_notional AS abs_close_notional,

    wf.realized_net_on_exchange_closes
        / NULLIF(wf.close_leg_abs_notional, 0.0) AS realized_vs_close_notional_ratio,

    wf.exchange_close_fill_count < 20 AS insufficient_evidence_gate,

    COALESCE(me.close_rows_missing_pnl_fee, 0) AS close_rows_without_pnl_fee_enrichment,

    (SELECT ts_start_w FROM params) AS window_started_at,

    (SELECT ts_anchor FROM params) AS window_ended_at
FROM wallet_facts wf
LEFT JOIN orphan_positions_agg pa
    ON pa.trader_id = wf.trader_id
LEFT JOIN orphan_closes_agg oc
    ON oc.trader_id = wf.trader_id
LEFT JOIN missing_enrichment me
    ON me.trader_id = wf.trader_id
ORDER BY wf.wallet;
