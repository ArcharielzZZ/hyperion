"""Persist wallet filter evaluation to Postgres."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from hyperion_pipeline.wallet_filter.models import WalletFilterResult
from hyperion_pipeline.wallet_filter.pipeline import daily_rows_for_persist

_FETCH_CONTEXT_SQL = text(
    """
    SELECT
        t.id AS trader_id,
        t.wallet_status,
        EXISTS (
            SELECT 1 FROM hyperliquid_whale_registry w
            WHERE lower(w.wallet) = lower(t.wallet)
        ) AS is_whale,
        (
            SELECT COUNT(*)::int FROM pnl_snapshots p
            WHERE p.trader_id = t.id
              AND p.timestamp >= NOW() - INTERVAL '30 days'
        ) AS pnl_snapshots_30d,
        (
            SELECT ts.total_score FROM trader_scores ts
            WHERE ts.trader_id = t.id
            ORDER BY ts.timestamp DESC
            LIMIT 1
        ) AS latest_behavior_score,
        (
            SELECT COUNT(*)::int FROM trader_scores ts
            WHERE ts.trader_id = t.id
        ) AS scoring_runs
    FROM traders t
    WHERE lower(t.wallet) = lower(:wallet)
    LIMIT 1
    """
)

_UPSERT_TRADER_FILTER_SQL = text(
    """
    UPDATE traders SET
        wallet_status = :status,
        wallet_label = :label,
        wallet_filter_updated_at = NOW(),
        behavior_tier = :behavior_tier,
        filter_discovery_score = :discovery_score,
        filter_avg_confidence = :avg_confidence
    WHERE id = :trader_id
    """
)

_UPSERT_PERFORMANCE_SQL = text(
    """
    INSERT INTO wallet_performance (
        trader_id, pnl_7d, trades_7d, pnl_1m, trades_1m, pnl_3m, trades_3m,
        pnl_6m, trades_6m, total_volume, total_trades, last_trade_at, updated_at
    ) VALUES (
        :trader_id, :pnl_7d, :trades_7d, :pnl_1m, :trades_1m, :pnl_3m, :trades_3m,
        :pnl_6m, :trades_6m, :total_volume, :total_trades, :last_trade_at, NOW()
    )
    ON CONFLICT (trader_id) DO UPDATE SET
        pnl_7d = EXCLUDED.pnl_7d,
        trades_7d = EXCLUDED.trades_7d,
        pnl_1m = EXCLUDED.pnl_1m,
        trades_1m = EXCLUDED.trades_1m,
        pnl_3m = EXCLUDED.pnl_3m,
        trades_3m = EXCLUDED.trades_3m,
        pnl_6m = EXCLUDED.pnl_6m,
        trades_6m = EXCLUDED.trades_6m,
        total_volume = EXCLUDED.total_volume,
        total_trades = EXCLUDED.total_trades,
        last_trade_at = EXCLUDED.last_trade_at,
        updated_at = NOW()
    """
)

_UPSERT_DAILY_SQL = text(
    """
    INSERT INTO wallet_daily_stats (trader_id, stats_date, daily_pnl, daily_trades, daily_volume)
    VALUES (:trader_id, :stats_date, :daily_pnl, :daily_trades, :daily_volume)
    ON CONFLICT (trader_id, stats_date) DO UPDATE SET
        daily_pnl = EXCLUDED.daily_pnl,
        daily_trades = EXCLUDED.daily_trades,
        daily_volume = EXCLUDED.daily_volume
    """
)

_SYNC_DISCOVERY_PROMOTED_SQL = text(
    """
    INSERT INTO trader_discovery_rankings (
        id, trader_id, wallet,
        public_trade_count_1h, public_trade_count_24h,
        public_notional_usd_1h, public_notional_usd_24h,
        active_coins_24h, buy_ratio_24h,
        fills_24h, positions_24h, pnl_snapshots_24h,
        last_public_trade_at, latest_behavior_score,
        data_coverage_score, activity_score, discovery_score,
        rank_tier, promoted, timestamp, created_at
    )
    SELECT
        gen_random_uuid(), t.id, t.wallet,
        0, 0, 0, 0, 0, 0.5,
        0, 0, 0,
        wp.last_trade_at,
        COALESCE(:behavior_score, 0),
        COALESCE(:data_completeness, 0),
        COALESCE(:activity_score, 0),
        COALESCE(:discovery_score, 0),
        :rank_tier,
        :promoted,
        NOW(), NOW()
    FROM traders t
    LEFT JOIN wallet_performance wp ON wp.trader_id = t.id
    WHERE t.id = :trader_id
    ON CONFLICT (trader_id) DO UPDATE SET
        discovery_score = EXCLUDED.discovery_score,
        data_coverage_score = EXCLUDED.data_coverage_score,
        activity_score = EXCLUDED.activity_score,
        latest_behavior_score = EXCLUDED.latest_behavior_score,
        rank_tier = EXCLUDED.rank_tier,
        promoted = EXCLUDED.promoted,
        last_public_trade_at = EXCLUDED.last_public_trade_at,
        timestamp = EXCLUDED.timestamp
    """
)


_FETCH_ALL_FILTER_CANDIDATES = text(
    """
    SELECT lower(t.wallet)::text AS wallet, t.id AS trader_id
    FROM traders t
    WHERE EXISTS (SELECT 1 FROM fills f WHERE f.trader_id = t.id LIMIT 1)
      AND COALESCE(t.wallet_status, 'PENDING') <> 'BANNED'
    ORDER BY t.last_seen DESC NULLS LAST
    """
)

_RESET_FILTER_STATE_SQL = text(
    """
    UPDATE traders SET
        wallet_status = 'PENDING',
        wallet_label = 'NEW_WALLET',
        wallet_filter_updated_at = NULL,
        behavior_tier = NULL,
        filter_discovery_score = NULL,
        filter_avg_confidence = NULL
    WHERE COALESCE(wallet_status, 'PENDING') <> 'BANNED'
    """
)

_DEMOTE_DISCOVERY_SQL = text(
    """
    UPDATE trader_discovery_rankings SET promoted = FALSE, rank_tier = 'watchlist'
    WHERE promoted IS TRUE
    """
)


async def fetch_wallets_for_filter_batch(engine: AsyncEngine) -> list[dict]:
    async with engine.connect() as conn:
        rows = (await conn.execute(_FETCH_ALL_FILTER_CANDIDATES)).mappings().all()
    return [dict(r) for r in rows]


async def reset_wallet_filter_state(engine: AsyncEngine) -> tuple[int, int]:
    """Clear filter evaluation (not BANNED) and demote discovery rankings before a full re-scoop."""

    async with engine.begin() as conn:
        r1 = await conn.execute(_RESET_FILTER_STATE_SQL)
        r2 = await conn.execute(_DEMOTE_DISCOVERY_SQL)
    return int(r1.rowcount or 0), int(r2.rowcount or 0)


async def fetch_wallet_filter_context(engine: AsyncEngine, wallet: str) -> dict | None:
    async with engine.connect() as conn:
        row = (await conn.execute(_FETCH_CONTEXT_SQL, {"wallet": wallet.lower()})).mappings().first()
    return dict(row) if row else None


async def persist_wallet_filter_result(
    engine: AsyncEngine,
    trader_id: uuid.UUID,
    result: WalletFilterResult,
    fills_for_daily: list,
    *,
    sync_discovery: bool = False,
) -> None:
    mw = result.multi_window
    tier = result.behavior_tier or "watchlist"
    rank_tier = {
        "high_quality": "high_quality",
        "active_tracked": "active_tracked",
        "watchlist": "watchlist",
    }.get(tier, "watchlist")

    async with engine.begin() as conn:
        await conn.execute(
            _UPSERT_TRADER_FILTER_SQL,
            {
                "trader_id": trader_id,
                "status": result.status,
                "label": result.label,
                "behavior_tier": result.behavior_tier,
                "discovery_score": result.discovery_score,
                "avg_confidence": result.avg_confidence,
            },
        )
        await conn.execute(
            _UPSERT_PERFORMANCE_SQL,
            {
                "trader_id": trader_id,
                "pnl_7d": mw.pnl_7d,
                "trades_7d": mw.trades_7d,
                "pnl_1m": mw.pnl_1m,
                "trades_1m": mw.trades_1m,
                "pnl_3m": mw.pnl_3m,
                "trades_3m": mw.trades_3m,
                "pnl_6m": mw.pnl_6m,
                "trades_6m": mw.trades_6m,
                "total_volume": mw.total_volume,
                "total_trades": mw.total_trades,
                "last_trade_at": mw.last_trade_at,
            },
        )
        daily = daily_rows_for_persist(fills_for_daily)
        for stats_date, (pnl, trades, vol) in daily.items():
            await conn.execute(
                _UPSERT_DAILY_SQL,
                {
                    "trader_id": trader_id,
                    "stats_date": stats_date,
                    "daily_pnl": pnl,
                    "daily_trades": trades,
                    "daily_volume": vol,
                },
            )

        if sync_discovery:
            activity = min(100.0, float(result.multi_window.trades_7d) * 5.0)
            await conn.execute(
                _SYNC_DISCOVERY_PROMOTED_SQL,
                {
                    "trader_id": trader_id,
                    "behavior_score": result.behavior_score,
                    "data_completeness": result.data_completeness_pct,
                    "activity_score": activity,
                    "discovery_score": result.discovery_score or 0.0,
                    "rank_tier": rank_tier,
                    "promoted": result.promoted,
                },
            )
