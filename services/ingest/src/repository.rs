use crate::normalizer::{
    MarketSnapshotRecord, NormalizedBatch, PnlSnapshotRecord, PositionRecord, TradeTickRecord,
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use hyperion_config::StorageConfig;
use serde_json::json;
use sqlx::{PgPool, Postgres, Transaction};
use uuid::Uuid;

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
    trader_last_seen_update_interval_secs: i64,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct PersistStats {
    pub attempted_records: u64,
    pub inserted_records: u64,
}

impl PersistStats {
    pub fn duplicate_records(self) -> u64 {
        self.attempted_records.saturating_sub(self.inserted_records)
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub struct MaintenanceStats {
    pub deleted_trade_ticks: u64,
    pub deleted_market_snapshots: u64,
}

impl Repository {
    pub fn new(pool: PgPool, storage: &StorageConfig) -> Self {
        Self {
            pool,
            trader_last_seen_update_interval_secs: storage
                .trader_last_seen_update_interval
                .as_secs() as i64,
        }
    }

    pub async fn persist_batch(&self, batch: &NormalizedBatch) -> Result<PersistStats> {
        let mut tx = self.pool.begin().await?;
        let mut stats = PersistStats {
            attempted_records: (batch.fills.len()
                + batch.positions.len()
                + batch.pnl_snapshots.len()
                + batch.market_snapshots.len()
                + batch.trade_ticks.len()) as u64,
            inserted_records: 0,
        };

        for fill in &batch.fills {
            let trader_id = ensure_trader(
                &mut tx,
                &fill.wallet,
                fill.timestamp,
                self.trader_last_seen_update_interval_secs,
            )
            .await?;
            let result = sqlx::query(
                r#"INSERT INTO fills (
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
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                 ON CONFLICT (event_key) DO UPDATE SET
                    fill_dir = COALESCE(EXCLUDED.fill_dir, fills.fill_dir),
                    closed_pnl_usd = COALESCE(EXCLUDED.closed_pnl_usd, fills.closed_pnl_usd),
                    fee_usd = COALESCE(EXCLUDED.fee_usd, fills.fee_usd)"#,
            )
            .bind(Uuid::new_v4())
            .bind(trader_id)
            .bind(&fill.coin)
            .bind(&fill.side)
            .bind(fill.size)
            .bind(fill.leverage)
            .bind(fill.price)
            .bind(fill.timestamp)
            .bind(&fill.event_key)
            .bind(fill.fill_dir.as_deref())
            .bind(fill.closed_pnl_usd)
            .bind(fill.fee_usd)
            .execute(&mut *tx)
            .await?;
            stats.inserted_records += result.rows_affected();
        }

        for position in &batch.positions {
            stats.inserted_records += persist_position(
                &mut tx,
                position,
                self.trader_last_seen_update_interval_secs,
            )
            .await?;
        }

        for snapshot in &batch.pnl_snapshots {
            stats.inserted_records += persist_pnl_snapshot(
                &mut tx,
                snapshot,
                self.trader_last_seen_update_interval_secs,
            )
            .await?;
        }

        for market in &batch.market_snapshots {
            stats.inserted_records += persist_market_snapshot(&mut tx, market).await?;
        }

        for trade in &batch.trade_ticks {
            stats.inserted_records +=
                persist_trade_tick(&mut tx, trade, self.trader_last_seen_update_interval_secs)
                    .await?;
        }

        tx.commit().await?;
        Ok(stats)
    }

    pub async fn run_storage_maintenance(
        &self,
        storage: &StorageConfig,
    ) -> Result<MaintenanceStats> {
        let deleted_trade_ticks = sqlx::query(
            "DELETE FROM trade_ticks
             WHERE timestamp < NOW() - ($1::BIGINT * INTERVAL '1 day')",
        )
        .bind(storage.trade_ticks_retention_days)
        .execute(&self.pool)
        .await?
        .rows_affected();

        let deleted_market_snapshots = sqlx::query(
            "DELETE FROM market_snapshots
             WHERE timestamp < NOW() - ($1::BIGINT * INTERVAL '1 day')",
        )
        .bind(storage.market_snapshots_retention_days)
        .execute(&self.pool)
        .await?
        .rows_affected();

        Ok(MaintenanceStats {
            deleted_trade_ticks,
            deleted_market_snapshots,
        })
    }

    /// Append-only Hyperliquid websocket lifecycle events (Week 1 audit trail).
    /// Best-effort: failures never break the ingest loop.
    pub async fn append_connection_event(&self, event_type: &str, detail: serde_json::Value) {
        if let Err(error) =
            sqlx::query("INSERT INTO ingest_connection_events (event_type, detail) VALUES ($1, $2)")
                .bind(event_type)
                .bind(detail)
                .execute(&self.pool)
                .await
        {
            tracing::warn!(
                ?error,
                event_type,
                "failed to persist ingest_connection_events row (migration 0010 applied?)"
            );
        }
    }

    pub async fn append_connection_event_simple(&self, event_type: &str, message: &str) {
        self.append_connection_event(event_type, json!({ "message": message }))
            .await;
    }
}

async fn ensure_trader(
    tx: &mut Transaction<'_, Postgres>,
    wallet: &str,
    seen_at: DateTime<Utc>,
    last_seen_update_interval_secs: i64,
) -> Result<Uuid> {
    let trader_id = sqlx::query_scalar::<_, Uuid>(
        r#"
        WITH upsert AS (
            INSERT INTO traders (wallet, first_seen, last_seen)
            VALUES ($1, $2, $2)
            ON CONFLICT (wallet) DO UPDATE
            SET last_seen = EXCLUDED.last_seen
            WHERE traders.last_seen < EXCLUDED.last_seen - ($3::BIGINT * INTERVAL '1 second')
            RETURNING id
        )
        SELECT id FROM upsert
        UNION ALL
        SELECT id FROM traders WHERE wallet = $1
        LIMIT 1
        "#,
    )
    .bind(wallet)
    .bind(seen_at)
    .bind(last_seen_update_interval_secs)
    .fetch_one(&mut **tx)
    .await?;

    Ok(trader_id)
}

async fn persist_position(
    tx: &mut Transaction<'_, Postgres>,
    position: &PositionRecord,
    last_seen_update_interval_secs: i64,
) -> Result<u64> {
    let trader_id = ensure_trader(
        tx,
        &position.wallet,
        position.timestamp,
        last_seen_update_interval_secs,
    )
    .await?;
    let result = sqlx::query(
        "INSERT INTO positions (
            id, trader_id, coin, direction, entry_price, size, leverage, unrealized_pnl, timestamp, snapshot_key
         ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
         ON CONFLICT (snapshot_key) DO NOTHING",
    )
    .bind(Uuid::new_v4())
    .bind(trader_id)
    .bind(&position.coin)
    .bind(&position.direction)
    .bind(position.entry_price)
    .bind(position.size)
    .bind(position.leverage)
    .bind(position.unrealized_pnl)
    .bind(position.timestamp)
    .bind(&position.snapshot_key)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected())
}

async fn persist_pnl_snapshot(
    tx: &mut Transaction<'_, Postgres>,
    snapshot: &PnlSnapshotRecord,
    last_seen_update_interval_secs: i64,
) -> Result<u64> {
    let trader_id = ensure_trader(
        tx,
        &snapshot.wallet,
        snapshot.timestamp,
        last_seen_update_interval_secs,
    )
    .await?;
    let result = sqlx::query(
        "INSERT INTO pnl_snapshots (
            id, trader_id, equity, realized_pnl, unrealized_pnl, timestamp, snapshot_key
         ) VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (snapshot_key) DO NOTHING",
    )
    .bind(Uuid::new_v4())
    .bind(trader_id)
    .bind(snapshot.equity)
    .bind(snapshot.realized_pnl)
    .bind(snapshot.unrealized_pnl)
    .bind(snapshot.timestamp)
    .bind(&snapshot.snapshot_key)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected())
}

async fn persist_market_snapshot(
    tx: &mut Transaction<'_, Postgres>,
    snapshot: &MarketSnapshotRecord,
) -> Result<u64> {
    let result = sqlx::query(
        "INSERT INTO market_snapshots (
            id, coin, funding_rate, open_interest, price, timestamp, snapshot_key
         ) VALUES ($1, $2, $3, $4, $5, $6, $7)
         ON CONFLICT (snapshot_key) DO NOTHING",
    )
    .bind(Uuid::new_v4())
    .bind(&snapshot.coin)
    .bind(snapshot.funding_rate)
    .bind(snapshot.open_interest)
    .bind(snapshot.price)
    .bind(snapshot.timestamp)
    .bind(&snapshot.snapshot_key)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected())
}

async fn persist_trade_tick(
    tx: &mut Transaction<'_, Postgres>,
    trade: &TradeTickRecord,
    last_seen_update_interval_secs: i64,
) -> Result<u64> {
    if let Some(wallet) = &trade.buyer_wallet {
        ensure_trader(tx, wallet, trade.timestamp, last_seen_update_interval_secs).await?;
    }
    if let Some(wallet) = &trade.seller_wallet {
        ensure_trader(tx, wallet, trade.timestamp, last_seen_update_interval_secs).await?;
    }

    let result = sqlx::query(
        "INSERT INTO trade_ticks (
            id, coin, side, price, size, trade_hash, trade_id, buyer_wallet, seller_wallet, timestamp, event_key
         ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
         ON CONFLICT (event_key) DO NOTHING",
    )
    .bind(Uuid::new_v4())
    .bind(&trade.coin)
    .bind(&trade.side)
    .bind(trade.price)
    .bind(trade.size)
    .bind(&trade.trade_hash)
    .bind(trade.trade_id)
    .bind(&trade.buyer_wallet)
    .bind(&trade.seller_wallet)
    .bind(trade.timestamp)
    .bind(&trade.event_key)
    .execute(&mut **tx)
    .await?;
    Ok(result.rows_affected())
}
