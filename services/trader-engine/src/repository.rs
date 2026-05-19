use anyhow::Result;
use chrono::{DateTime, Utc};
use hyperion_models::{
    BehavioralAlert, Fill, PnlSnapshot, Position, Trader, TraderBehaviorProfile,
    TraderDiscoveryRank, TraderScore,
};
use hyperion_schemas::{LeaderboardItem, LeaderboardQuery, TraderDetailResponse};
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
}

pub struct BehaviorInput {
    pub trader: Trader,
    pub fills: Vec<Fill>,
    pub positions: Vec<Position>,
    pub pnl_snapshots: Vec<PnlSnapshot>,
    pub entry_timing_samples: Vec<EntryTimingSample>,
}

#[derive(Debug, Clone, FromRow)]
pub struct TraderDiscoveryAggregate {
    pub trader_id: Uuid,
    pub wallet: String,
    pub wallet_status: Option<String>,
    pub wallet_filter_updated_at: Option<DateTime<Utc>>,
    pub behavior_tier: Option<String>,
    pub public_trade_count_1h: i64,
    pub public_trade_count_24h: i64,
    pub public_notional_usd_1h: f64,
    pub public_notional_usd_24h: f64,
    pub active_coins_24h: i64,
    pub buy_ratio_24h: f64,
    pub fills_24h: i64,
    pub positions_24h: i64,
    pub pnl_snapshots_24h: i64,
    pub last_public_trade_at: Option<DateTime<Utc>>,
    pub latest_behavior_score: Option<f64>,
}

#[derive(Debug, Clone, FromRow)]
pub struct ScoreTraderCandidate {
    pub trader_id: Uuid,
    pub wallet: String,
    pub fills_30d: i64,
    pub positions_30d: i64,
    pub pnl_snapshots_30d: i64,
    pub active_days_30d: i64,
}

#[derive(Debug, FromRow)]
struct TraderRow {
    id: Uuid,
    wallet: String,
}

#[derive(Debug, Clone, FromRow)]
pub struct EntryTimingSample {
    pub side: String,
    pub fill_price: f64,
    pub future_price: Option<f64>,
    #[allow(dead_code)]
    pub fill_timestamp: DateTime<Utc>,
}

impl Repository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn fetch_score_candidates(
        &self,
        min_active_days: i64,
        min_trade_observations: i64,
        min_pnl_snapshots: i64,
    ) -> Result<Vec<ScoreTraderCandidate>> {
        let rows = sqlx::query_as::<_, ScoreTraderCandidate>(
            r#"
            WITH candidates AS (
                SELECT
                    t.id AS trader_id,
                    t.wallet,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM fills f
                        WHERE f.trader_id = t.id
                          AND f.timestamp > NOW() - INTERVAL '30 days'
                    ), 0) AS fills_30d,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM positions p
                        WHERE p.trader_id = t.id
                          AND p.timestamp > NOW() - INTERVAL '30 days'
                    ), 0) AS positions_30d,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM pnl_snapshots ps
                        WHERE ps.trader_id = t.id
                          AND ps.timestamp > NOW() - INTERVAL '30 days'
                    ), 0) AS pnl_snapshots_30d,
                    COALESCE((
                        SELECT COUNT(DISTINCT DATE(sample_ts))
                        FROM (
                            SELECT f.timestamp AS sample_ts
                            FROM fills f
                            WHERE f.trader_id = t.id
                              AND f.timestamp > NOW() - INTERVAL '30 days'
                            UNION ALL
                            SELECT p.timestamp AS sample_ts
                            FROM positions p
                            WHERE p.trader_id = t.id
                              AND p.timestamp > NOW() - INTERVAL '30 days'
                            UNION ALL
                            SELECT ps.timestamp AS sample_ts
                            FROM pnl_snapshots ps
                            WHERE ps.trader_id = t.id
                              AND ps.timestamp > NOW() - INTERVAL '30 days'
                        ) samples
                    ), 0) AS active_days_30d
                FROM traders t
                WHERE t.last_seen > NOW() - INTERVAL '30 days'
                  AND (
                    (t.wallet_filter_updated_at IS NOT NULL AND t.wallet_status = 'PROMOTED')
                    OR (
                      t.wallet_filter_updated_at IS NULL
                      AND EXISTS (
                        SELECT 1 FROM trader_discovery_rankings r
                        WHERE r.trader_id = t.id AND r.promoted = TRUE
                      )
                    )
                  )
            )
            SELECT trader_id, wallet, fills_30d, positions_30d, pnl_snapshots_30d, active_days_30d
            FROM candidates
            WHERE active_days_30d >= $1
              AND (fills_30d + positions_30d) >= $2
              AND pnl_snapshots_30d >= $3
            ORDER BY pnl_snapshots_30d DESC, (fills_30d + positions_30d) DESC, active_days_30d DESC
            "#,
        )
        .bind(min_active_days)
        .bind(min_trade_observations)
        .bind(min_pnl_snapshots)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn insert_score(&self, score: TraderScore) -> Result<()> {
        sqlx::query(
            "INSERT INTO trader_scores (
                id, trader_id, consistency_score, survivability_score, timing_score,
                leverage_discipline_score, conviction_score, total_score, style, timestamp
             ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
        )
        .bind(score.id)
        .bind(score.trader_id)
        .bind(score.consistency_score)
        .bind(score.survivability_score)
        .bind(score.timing_score)
        .bind(score.leverage_discipline_score)
        .bind(score.conviction_score)
        .bind(score.total_score)
        .bind(score.style)
        .bind(score.timestamp)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn fetch_behavior_input(&self, trader_id: Uuid) -> Result<Option<BehaviorInput>> {
        let trader = sqlx::query_as::<_, Trader>(
            "SELECT id, wallet, first_seen, last_seen, created_at FROM traders WHERE id = $1",
        )
        .bind(trader_id)
        .fetch_optional(&self.pool)
        .await?;
        let Some(trader) = trader else {
            return Ok(None);
        };

        let fills = sqlx::query_as::<_, Fill>(
            "SELECT id, trader_id, coin, side, size, leverage, price, timestamp, created_at
             FROM fills
             WHERE trader_id = $1 AND timestamp > NOW() - INTERVAL '90 days'
             ORDER BY timestamp ASC",
        )
        .bind(trader_id)
        .fetch_all(&self.pool)
        .await?;

        let positions = sqlx::query_as::<_, Position>(
            "SELECT id, trader_id, coin, direction, entry_price, size, leverage, unrealized_pnl, timestamp, created_at
             FROM positions
             WHERE trader_id = $1 AND timestamp > NOW() - INTERVAL '90 days'
             ORDER BY timestamp ASC",
        )
        .bind(trader_id)
        .fetch_all(&self.pool)
        .await?;

        let pnl_snapshots = sqlx::query_as::<_, PnlSnapshot>(
            "SELECT id, trader_id, equity, realized_pnl, unrealized_pnl, timestamp, created_at
             FROM pnl_snapshots
             WHERE trader_id = $1 AND timestamp > NOW() - INTERVAL '90 days'
             ORDER BY timestamp ASC",
        )
        .bind(trader_id)
        .fetch_all(&self.pool)
        .await?;

        let entry_timing_samples = sqlx::query_as::<_, EntryTimingSample>(
            r#"
            SELECT
                f.side,
                f.price AS fill_price,
                future.price AS future_price,
                f.timestamp AS fill_timestamp
            FROM fills f
            LEFT JOIN LATERAL (
                SELECT m.price
                FROM market_snapshots m
                WHERE m.coin = f.coin
                  AND m.timestamp >= f.timestamp + INTERVAL '15 minutes'
                ORDER BY m.timestamp ASC
                LIMIT 1
            ) future ON TRUE
            WHERE f.trader_id = $1
              AND f.timestamp > NOW() - INTERVAL '30 days'
            ORDER BY f.timestamp ASC
            "#,
        )
        .bind(trader_id)
        .fetch_all(&self.pool)
        .await?;

        Ok(Some(BehaviorInput {
            trader,
            fills,
            positions,
            pnl_snapshots,
            entry_timing_samples,
        }))
    }

    pub async fn insert_behavior_profile(&self, profile: TraderBehaviorProfile) -> Result<()> {
        sqlx::query(
            r#"
            INSERT INTO trader_behavior_profiles (
                id, trader_id, rolling_consistency_score_7d, rolling_consistency_score_30d,
                avg_daily_pnl_7d, avg_daily_pnl_30d, pnl_volatility_7d, pnl_volatility_30d,
                win_rate_30d, recent_leverage_avg_7d, leverage_avg_30d, leverage_peak_30d,
                leverage_volatility_30d, max_drawdown_pct_30d, avg_hold_duration_secs_30d,
                median_hold_duration_secs_30d, entry_timing_edge_bps_30d, favorable_entry_rate_30d,
                behavioral_drift_score, emotional_volatility_score, revenge_trading_score,
                sizing_instability_score, consistency_score_delta, active_days_30d,
                fills_7d, fills_30d, lifecycle_stage, window_start, window_end, timestamp
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                $21,$22,$23,$24,$25,$26,$27,$28,$29,$30
            )
            ON CONFLICT DO NOTHING
            "#,
        )
        .bind(profile.id)
        .bind(profile.trader_id)
        .bind(profile.rolling_consistency_score_7d)
        .bind(profile.rolling_consistency_score_30d)
        .bind(profile.avg_daily_pnl_7d)
        .bind(profile.avg_daily_pnl_30d)
        .bind(profile.pnl_volatility_7d)
        .bind(profile.pnl_volatility_30d)
        .bind(profile.win_rate_30d)
        .bind(profile.recent_leverage_avg_7d)
        .bind(profile.leverage_avg_30d)
        .bind(profile.leverage_peak_30d)
        .bind(profile.leverage_volatility_30d)
        .bind(profile.max_drawdown_pct_30d)
        .bind(profile.avg_hold_duration_secs_30d)
        .bind(profile.median_hold_duration_secs_30d)
        .bind(profile.entry_timing_edge_bps_30d)
        .bind(profile.favorable_entry_rate_30d)
        .bind(profile.behavioral_drift_score)
        .bind(profile.emotional_volatility_score)
        .bind(profile.revenge_trading_score)
        .bind(profile.sizing_instability_score)
        .bind(profile.consistency_score_delta)
        .bind(profile.active_days_30d)
        .bind(profile.fills_7d)
        .bind(profile.fills_30d)
        .bind(&profile.lifecycle_stage)
        .bind(profile.window_start)
        .bind(profile.window_end)
        .bind(profile.timestamp)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn insert_behavior_alert(&self, alert: BehavioralAlert) -> Result<()> {
        sqlx::query(
            r#"
            INSERT INTO behavioral_alerts (
                id, trader_id, alert_type, severity, title, message,
                metric_value, threshold_value, detected_at, dedupe_key
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (dedupe_key) DO NOTHING
            "#,
        )
        .bind(alert.id)
        .bind(alert.trader_id)
        .bind(&alert.alert_type)
        .bind(&alert.severity)
        .bind(&alert.title)
        .bind(&alert.message)
        .bind(alert.metric_value)
        .bind(alert.threshold_value)
        .bind(alert.detected_at)
        .bind(&alert.dedupe_key)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn fetch_trader_discovery_aggregates(&self) -> Result<Vec<TraderDiscoveryAggregate>> {
        let rows = sqlx::query_as::<_, TraderDiscoveryAggregate>(
            r#"
            WITH public_flow AS (
                SELECT buyer_wallet AS wallet, coin, price, size, timestamp, TRUE AS is_buy
                FROM trade_ticks
                WHERE buyer_wallet IS NOT NULL
                  AND timestamp > NOW() - INTERVAL '24 hours'
                UNION ALL
                SELECT seller_wallet AS wallet, coin, price, size, timestamp, FALSE AS is_buy
                FROM trade_ticks
                WHERE seller_wallet IS NOT NULL
                  AND timestamp > NOW() - INTERVAL '24 hours'
            )
            SELECT
                t.id AS trader_id,
                t.wallet,
                t.wallet_status,
                t.wallet_filter_updated_at,
                t.behavior_tier,
                COUNT(*) FILTER (WHERE pf.timestamp > NOW() - INTERVAL '1 hour') AS public_trade_count_1h,
                COUNT(*) FILTER (WHERE pf.timestamp > NOW() - INTERVAL '24 hours') AS public_trade_count_24h,
                COALESCE(SUM(pf.price * pf.size) FILTER (WHERE pf.timestamp > NOW() - INTERVAL '1 hour'), 0.0)
                    AS public_notional_usd_1h,
                COALESCE(SUM(pf.price * pf.size) FILTER (WHERE pf.timestamp > NOW() - INTERVAL '24 hours'), 0.0)
                    AS public_notional_usd_24h,
                COUNT(DISTINCT pf.coin) FILTER (WHERE pf.timestamp > NOW() - INTERVAL '24 hours')
                    AS active_coins_24h,
                COALESCE(
                    AVG(CASE WHEN pf.is_buy THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE pf.timestamp > NOW() - INTERVAL '24 hours'),
                    0.5
                )::DOUBLE PRECISION AS buy_ratio_24h,
                COALESCE((
                    SELECT COUNT(*)
                    FROM fills f
                    WHERE f.trader_id = t.id AND f.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) AS fills_24h,
                COALESCE((
                    SELECT COUNT(*)
                    FROM positions p
                    WHERE p.trader_id = t.id AND p.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) AS positions_24h,
                COALESCE((
                    SELECT COUNT(*)
                    FROM pnl_snapshots ps
                    WHERE ps.trader_id = t.id AND ps.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) AS pnl_snapshots_24h,
                MAX(pf.timestamp) AS last_public_trade_at,
                (
                    SELECT s.total_score
                    FROM trader_scores s
                    WHERE s.trader_id = t.id
                      AND s.timestamp > NOW() - INTERVAL '15 minutes'
                    ORDER BY s.timestamp DESC
                    LIMIT 1
                ) AS latest_behavior_score
            FROM traders t
            LEFT JOIN public_flow pf ON pf.wallet = t.wallet
            WHERE t.last_seen > NOW() - INTERVAL '30 days'
            GROUP BY t.id, t.wallet, t.wallet_status, t.wallet_filter_updated_at, t.behavior_tier
            HAVING COUNT(pf.*) > 0
                OR COALESCE((
                    SELECT COUNT(*)
                    FROM fills f
                    WHERE f.trader_id = t.id AND f.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) > 0
                OR COALESCE((
                    SELECT COUNT(*)
                    FROM positions p
                    WHERE p.trader_id = t.id AND p.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) > 0
                OR COALESCE((
                    SELECT COUNT(*)
                    FROM pnl_snapshots ps
                    WHERE ps.trader_id = t.id AND ps.timestamp > NOW() - INTERVAL '24 hours'
                ), 0) > 0
            ORDER BY public_trade_count_24h DESC, public_notional_usd_24h DESC, t.last_seen DESC
            "#,
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn upsert_discovery_rank(&self, rank: TraderDiscoveryRank) -> Result<()> {
        sqlx::query(
            r#"
            INSERT INTO trader_discovery_rankings (
                id, trader_id, wallet, public_trade_count_1h, public_trade_count_24h,
                public_notional_usd_1h, public_notional_usd_24h, active_coins_24h, buy_ratio_24h,
                fills_24h, positions_24h, pnl_snapshots_24h, last_public_trade_at,
                latest_behavior_score, data_coverage_score, activity_score, discovery_score,
                rank_tier, promoted, timestamp
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20
            )
            ON CONFLICT (trader_id) DO UPDATE SET
                wallet = EXCLUDED.wallet,
                public_trade_count_1h = EXCLUDED.public_trade_count_1h,
                public_trade_count_24h = EXCLUDED.public_trade_count_24h,
                public_notional_usd_1h = EXCLUDED.public_notional_usd_1h,
                public_notional_usd_24h = EXCLUDED.public_notional_usd_24h,
                active_coins_24h = EXCLUDED.active_coins_24h,
                buy_ratio_24h = EXCLUDED.buy_ratio_24h,
                fills_24h = EXCLUDED.fills_24h,
                positions_24h = EXCLUDED.positions_24h,
                pnl_snapshots_24h = EXCLUDED.pnl_snapshots_24h,
                last_public_trade_at = EXCLUDED.last_public_trade_at,
                latest_behavior_score = EXCLUDED.latest_behavior_score,
                data_coverage_score = EXCLUDED.data_coverage_score,
                activity_score = EXCLUDED.activity_score,
                discovery_score = EXCLUDED.discovery_score,
                rank_tier = EXCLUDED.rank_tier,
                promoted = EXCLUDED.promoted,
                timestamp = EXCLUDED.timestamp
            WHERE trader_discovery_rankings.wallet IS DISTINCT FROM EXCLUDED.wallet
               OR trader_discovery_rankings.public_trade_count_1h IS DISTINCT FROM EXCLUDED.public_trade_count_1h
               OR trader_discovery_rankings.public_trade_count_24h IS DISTINCT FROM EXCLUDED.public_trade_count_24h
               OR trader_discovery_rankings.public_notional_usd_1h IS DISTINCT FROM EXCLUDED.public_notional_usd_1h
               OR trader_discovery_rankings.public_notional_usd_24h IS DISTINCT FROM EXCLUDED.public_notional_usd_24h
               OR trader_discovery_rankings.active_coins_24h IS DISTINCT FROM EXCLUDED.active_coins_24h
               OR trader_discovery_rankings.buy_ratio_24h IS DISTINCT FROM EXCLUDED.buy_ratio_24h
               OR trader_discovery_rankings.fills_24h IS DISTINCT FROM EXCLUDED.fills_24h
               OR trader_discovery_rankings.positions_24h IS DISTINCT FROM EXCLUDED.positions_24h
               OR trader_discovery_rankings.pnl_snapshots_24h IS DISTINCT FROM EXCLUDED.pnl_snapshots_24h
               OR trader_discovery_rankings.last_public_trade_at IS DISTINCT FROM EXCLUDED.last_public_trade_at
               OR trader_discovery_rankings.latest_behavior_score IS DISTINCT FROM EXCLUDED.latest_behavior_score
               OR trader_discovery_rankings.data_coverage_score IS DISTINCT FROM EXCLUDED.data_coverage_score
               OR trader_discovery_rankings.activity_score IS DISTINCT FROM EXCLUDED.activity_score
               OR trader_discovery_rankings.discovery_score IS DISTINCT FROM EXCLUDED.discovery_score
               OR trader_discovery_rankings.rank_tier IS DISTINCT FROM EXCLUDED.rank_tier
               OR trader_discovery_rankings.promoted IS DISTINCT FROM EXCLUDED.promoted
            "#,
        )
        .bind(rank.id)
        .bind(rank.trader_id)
        .bind(&rank.wallet)
        .bind(rank.public_trade_count_1h)
        .bind(rank.public_trade_count_24h)
        .bind(rank.public_notional_usd_1h)
        .bind(rank.public_notional_usd_24h)
        .bind(rank.active_coins_24h)
        .bind(rank.buy_ratio_24h)
        .bind(rank.fills_24h)
        .bind(rank.positions_24h)
        .bind(rank.pnl_snapshots_24h)
        .bind(rank.last_public_trade_at)
        .bind(rank.latest_behavior_score)
        .bind(rank.data_coverage_score)
        .bind(rank.activity_score)
        .bind(rank.discovery_score)
        .bind(&rank.rank_tier)
        .bind(rank.promoted)
        .bind(rank.timestamp)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    pub async fn leaderboard(&self, query: &LeaderboardQuery) -> Result<Vec<LeaderboardItem>> {
        let limit = query.pagination.limit.clamp(1, 100);
        let offset = (query.pagination.page.max(1) - 1) * limit;
        let rows = sqlx::query_as::<_, LeaderboardItem>(
            r#"
            SELECT
                t.wallet,
                ts.total_score,
                ts.style,
                t.last_seen
            FROM traders t
            JOIN trader_discovery_rankings r
              ON r.trader_id = t.id
             AND r.promoted = TRUE
            JOIN LATERAL (
                SELECT total_score, style
                FROM trader_scores s
                WHERE s.trader_id = t.id
                  AND s.timestamp > NOW() - INTERVAL '15 minutes'
                ORDER BY s.timestamp DESC
                LIMIT 1
            ) ts ON TRUE
            WHERE ($1::DOUBLE PRECISION IS NULL OR ts.total_score >= $1)
              AND ($2::TEXT IS NULL OR ts.style = $2)
              AND (
                    EXISTS (SELECT 1 FROM fills f WHERE f.trader_id = t.id)
                    OR EXISTS (SELECT 1 FROM positions p WHERE p.trader_id = t.id)
                    OR EXISTS (SELECT 1 FROM pnl_snapshots ps WHERE ps.trader_id = t.id)
              )
            ORDER BY ts.total_score DESC, t.last_seen DESC
            LIMIT $3 OFFSET $4
            "#,
        )
        .bind(query.min_score)
        .bind(query.style.clone())
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn trader_detail(&self, wallet: &str) -> Result<Option<TraderDetailResponse>> {
        let trader =
            sqlx::query_as::<_, TraderRow>("SELECT id, wallet FROM traders WHERE wallet = $1")
                .bind(wallet)
                .fetch_optional(&self.pool)
                .await?;

        let Some(trader) = trader else {
            return Ok(None);
        };

        let latest_score = sqlx::query_as::<_, TraderScore>(
            r#"
            SELECT id, trader_id, consistency_score, survivability_score, timing_score,
                   leverage_discipline_score, conviction_score, total_score, style, timestamp, created_at
            FROM trader_scores
            WHERE trader_id = $1
              AND timestamp > NOW() - INTERVAL '15 minutes'
            ORDER BY timestamp DESC
            LIMIT 1
            "#,
        )
        .bind(trader.id)
        .fetch_optional(&self.pool)
        .await?;

        Ok(Some(TraderDetailResponse {
            trader_id: trader.id,
            wallet: trader.wallet,
            latest_score,
        }))
    }

    /// Curated whale wallets (leaderboard discovery); survives discovery rank refresh.
    pub async fn fetch_whale_registry_wallets(&self) -> Result<Vec<String>> {
        let rows = sqlx::query_scalar::<_, String>(
            "SELECT lower(wallet) FROM hyperliquid_whale_registry ORDER BY all_time_vlm_usd DESC",
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }
}
