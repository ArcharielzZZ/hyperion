"""Insert normalized fills into Postgres (same rules as Rust ``ingest``)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from hyperion_pipeline.ingestion.fill_normalizer import FillNormalizer
from hyperion_pipeline.models.pydantic_domain import TradeFill

logger = structlog.get_logger(__name__)

_ENSURE_TRADER_SQL = text(
    """
    WITH upsert AS (
        INSERT INTO traders (wallet, first_seen, last_seen)
        VALUES (:wallet, :seen, :seen)
        ON CONFLICT (wallet) DO UPDATE
        SET last_seen = EXCLUDED.last_seen
        WHERE traders.last_seen < EXCLUDED.last_seen - (:interval * INTERVAL '1 second')
        RETURNING id
    )
    SELECT id FROM upsert
    UNION ALL
    SELECT id FROM traders WHERE wallet = :wallet
    LIMIT 1
    """
)

_FETCH_FILLS_FOR_WALLET = text(
    """
    SELECT f.coin, f.side, f.size, f.leverage, f.price, f.timestamp, f.event_key,
           f.fill_dir, f.closed_pnl_usd, f.fee_usd
    FROM fills f
    INNER JOIN traders t ON t.id = f.trader_id
    WHERE lower(t.wallet) = lower(:wallet)
      AND f.timestamp >= COALESCE(CAST(:ts_start AS TIMESTAMP WITH TIME ZONE), '-infinity'::timestamptz)
      AND f.timestamp <= COALESCE(CAST(:ts_end AS TIMESTAMP WITH TIME ZONE), 'infinity'::timestamptz)
    ORDER BY f.timestamp ASC, f.event_key ASC
    """
)

_FETCH_PROMOTED_WALLET_ADDRESSES = text(
    """
    SELECT lower(t.wallet)::text AS wallet
    FROM traders t
    WHERE (
        t.wallet_filter_updated_at IS NOT NULL
        AND t.wallet_status = 'PROMOTED'
    )
    OR (
        t.wallet_filter_updated_at IS NULL
        AND COALESCE(
            (
                SELECT dr.promoted
                FROM trader_discovery_rankings dr
                WHERE dr.trader_id = t.id
                ORDER BY dr.timestamp DESC
                LIMIT 1
            ),
            FALSE
        ) IS TRUE
    )
    ORDER BY wallet
    """
)

_COUNT_FILLS_FOR_WALLET = text(
    """
    SELECT COUNT(*)::bigint AS n
    FROM fills f
    INNER JOIN traders t ON t.id = f.trader_id
    WHERE lower(t.wallet) = lower(:wallet)
    """
)


async def count_fills_for_wallet(engine: AsyncEngine, wallet: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(_COUNT_FILLS_FOR_WALLET, {"wallet": wallet.lower()})).scalar_one())


async def fetch_trade_fills_for_wallet(
    engine: AsyncEngine,
    wallet: str,
    *,
    ts_start: datetime | None = None,
    ts_end: datetime | None = None,
) -> list[TradeFill]:
    """Load normalized fills from OLTP ``fills`` (same ordering as ingest / backtest)."""

    w = wallet.lower()
    fills: list[TradeFill] = []
    async with engine.connect() as conn:
        async with conn.stream(
            _FETCH_FILLS_FOR_WALLET,
            {"wallet": w, "ts_start": ts_start, "ts_end": ts_end},
        ) as result:
            async for row in result.mappings():
                ts = row["timestamp"]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                side = FillNormalizer._normalize_side(str(row["side"]))
                fills.append(
                    TradeFill(
                        wallet=w,
                        coin=str(row["coin"]),
                        side=side,
                        size=float(row["size"]),
                        leverage=float(row["leverage"] or 0.0),
                        price=float(row["price"]),
                        event_timestamp=ts,
                        event_key=str(row["event_key"]),
                        source="postgres_ingest",
                        fill_dir=str(row["fill_dir"]) if row.get("fill_dir") is not None else None,
                        closed_pnl_usd=float(row["closed_pnl_usd"])
                        if row.get("closed_pnl_usd") is not None
                        else None,
                        fee_usd=float(row["fee_usd"]) if row.get("fee_usd") is not None else None,
                        raw={},
                    )
                )
    return fills


async def fetch_promoted_wallet_addresses(engine: AsyncEngine) -> list[str]:
    """
    Addresses with ``promoted`` true on latest ``trader_discovery_rankings`` row per trader.

    Gotcha: if latest snapshot demotes a trader, wallet drops from results even though history
           still carries ``promoted`` on older timestamps.
    """

    wallets: list[str] = []
    async with engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    _FETCH_PROMOTED_WALLET_ADDRESSES,
                )
            )
            .mappings()
            .all()
        )
    wallets = [str(r["wallet"]) for r in rows]
    return wallets


_WALLET_META_SQL = text(
    """
    SELECT w.hit_rate_pct, w.all_time_vlm_usd, w.all_time_roi,
           dr.discovery_score, dr.rank_tier, dr.promoted
    FROM traders t
    LEFT JOIN hyperliquid_whale_registry w ON lower(w.wallet) = lower(t.wallet)
    LEFT JOIN LATERAL (
        SELECT discovery_score, rank_tier, promoted
        FROM trader_discovery_rankings r
        WHERE r.trader_id = t.id
        ORDER BY r.timestamp DESC
        LIMIT 1
    ) dr ON true
    WHERE lower(t.wallet) = lower(:wallet)
    """
)


async def fetch_wallet_meta(engine: AsyncEngine, wallet: str) -> dict[str, Any]:
    """Registry + latest discovery row for markdown appendix."""

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                _WALLET_META_SQL,
                {"wallet": wallet.lower()},
            )
        ).mappings().first()
    return dict(row) if row else {}


_INSERT_FILL_SQL = text(
    """
    INSERT INTO fills (
        id,
        trader_id,
        coin,
        side,
        size,
        leverage,
        price,
        timestamp,
        event_key,
        fill_dir,
        closed_pnl_usd,
        fee_usd
    )
    VALUES (:id, :trader_id, :coin, :side, :size, :leverage, :price, :ts, :event_key,
            :fill_dir, :closed_pnl_usd, :fee_usd)
    ON CONFLICT (event_key) DO UPDATE SET
        fill_dir = COALESCE(EXCLUDED.fill_dir, fills.fill_dir),
        closed_pnl_usd = COALESCE(EXCLUDED.closed_pnl_usd, fills.closed_pnl_usd),
        fee_usd = COALESCE(EXCLUDED.fee_usd, fills.fee_usd)
    """
)

_SYNC_STATE_SQL = text(
    """
    INSERT INTO wallet_sync_state (
        wallet_address, source, sync_status, cursor_state, last_watermark, last_error, updated_at, created_at
    )
    VALUES (
        :wallet, 'hyperliquid', :status, CAST(:cursor_json AS jsonb), :watermark, :err, NOW(), NOW()
    )
    ON CONFLICT (wallet_address) DO UPDATE SET
        sync_status = EXCLUDED.sync_status,
        cursor_state = EXCLUDED.cursor_state,
        last_watermark = EXCLUDED.last_watermark,
        last_error = EXCLUDED.last_error,
        updated_at = NOW()
    """
)


async def _ensure_trader(
    conn: AsyncConnection,
    wallet: str,
    seen_at: datetime,
    interval_secs: int,
) -> uuid.UUID:
    result = await conn.execute(
        _ENSURE_TRADER_SQL,
        {"wallet": wallet, "seen": seen_at, "interval": interval_secs},
    )
    return result.scalar_one()


async def insert_fills_tx(
    conn: AsyncConnection,
    fills: list[TradeFill],
    *,
    trader_last_seen_interval_secs: int,
) -> tuple[int, int]:
    """
    Insert fills in one transaction (caller provides ``conn``).

    Returns ``(attempted, inserted_rows)`` where ``inserted_rows`` counts new ``fills`` rows.
    """

    attempted = len(fills)
    inserted = 0
    sorted_fills = sorted(fills, key=lambda f: f.event_timestamp)
    for fill in sorted_fills:
        trader_id = await _ensure_trader(conn, fill.wallet, fill.event_timestamp, trader_last_seen_interval_secs)
        res = await conn.execute(
            _INSERT_FILL_SQL,
            {
                "id": uuid.uuid4(),
                "trader_id": trader_id,
                "coin": fill.coin,
                "side": fill.side,
                "size": fill.size,
                "leverage": fill.leverage,
                "price": fill.price,
                "ts": fill.event_timestamp,
                "event_key": fill.event_key,
                "fill_dir": fill.fill_dir,
                "closed_pnl_usd": fill.closed_pnl_usd,
                "fee_usd": fill.fee_usd,
            },
        )
        inserted += int(res.rowcount or 0)
    return attempted, inserted


async def upsert_fills(
    engine: AsyncEngine,
    fills: list[TradeFill],
    *,
    trader_last_seen_interval_secs: int,
) -> tuple[int, int]:
    """Run ``insert_fills_tx`` inside ``engine.begin()``."""

    if not fills:
        return 0, 0
    async with engine.begin() as conn:
        return await insert_fills_tx(conn, fills, trader_last_seen_interval_secs=trader_last_seen_interval_secs)


async def update_wallet_sync_state(
    engine: AsyncEngine,
    wallet: str,
    *,
    status: str,
    cursor: dict[str, Any],
    last_watermark: datetime | None,
    error: str | None,
) -> None:
    """Upsert ``wallet_sync_state`` after a backfill attempt."""

    async with engine.begin() as conn:
        await conn.execute(
            _SYNC_STATE_SQL,
            {
                "wallet": wallet.lower(),
                "status": status,
                "cursor_json": json.dumps(cursor),
                "watermark": last_watermark,
                "err": error,
            },
        )
    logger.info("wallet_sync_state_updated", wallet=wallet.lower(), status=status)
