-- =============================================================================
-- COPY TRADE — ROUND TRIP AUDIT (wallet allowlist cohort)
-- -----------------------------------------------------------------------------
-- Same semantics as copy_trade_roundtrip_audit.sql, but restricts the cohort
-- to explicit wallet addresses (no promoted-discovery join). Edit VALUES below.
-- -----------------------------------------------------------------------------

WITH params AS (
    SELECT
        CURRENT_TIMESTAMP AT TIME ZONE 'utc' AS ts_anchor,
        CURRENT_TIMESTAMP AT TIME ZONE 'utc' - INTERVAL '30 days' AS ts_start_w
),

target_wallets(wallet) AS (
    SELECT lower(trim(x))::text
    FROM (
        VALUES
            ('0x388d9e1ccaec74527f73f8c87b5928ba28c06c03'),
            ('0x5a7581618829f377a16be2338eabdd03fece0eaf'),
            ('0x06438b0d1bb6f8aa4a455a4f2c1b1e744d53c760'),
            ('0x0c684f333a7e120bce61383da670bbb0157e82d0'),
            ('0xca230e816bdb34a46960c2f978a30a563d1ae9e0'),
            ('0x4efe2304c67324c772b733610ae76e2079e18075'),
            ('0x8184405076b61ab78013f16393322eaf0d0a01a1'),
            ('0x8e80c4b533dd977cf716b5c24fd9223129272804'),
            ('0x72cb918356c4f6d3f1b2e532928110ba6995f139'),
            ('0x1a2e6afa298b1cc50939b4b2b0b430e3c1ef3459')
    ) AS v(x)
),

promoted_latest AS (
    SELECT t.id AS trader_id, t.wallet::text AS wallet
    FROM traders t
    INNER JOIN target_wallets tw ON lower(t.wallet) = tw.wallet
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
