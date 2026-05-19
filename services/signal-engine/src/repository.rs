use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use hyperion_models::{Signal, SignalOutcome};
use hyperion_schemas::SignalQuery;
use sqlx::{FromRow, PgPool};
use uuid::Uuid;

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
}

#[derive(Debug, Clone, FromRow)]
pub struct SignalCandidate {
    pub coin: String,
    pub direction: String,
    pub supporting_traders: i64,
    pub avg_score: f64,
    pub quality_weight: f64,
    pub funding_rate: f64,
    pub open_interest: f64,
    pub reference_price: f64,
    pub momentum_5m_bps: f64,
    pub momentum_15m_bps: f64,
    pub volatility_1h_bps: f64,
    pub latest_entry_timestamp: DateTime<Utc>,
    pub window_start: DateTime<Utc>,
    pub window_end: DateTime<Utc>,
}

#[derive(Debug, Clone, FromRow)]
pub struct SignalContributorCandidate {
    pub trader_id: Uuid,
    pub wallet: String,
    pub trader_score: f64,
    pub weight: f64,
    pub entry_price: f64,
    pub entry_timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, FromRow)]
pub struct DueOutcomeCandidate {
    pub signal_id: Uuid,
    pub coin: String,
    pub direction: String,
    pub reference_price: f64,
    pub signal_window_end: DateTime<Utc>,
}

#[derive(Debug, Clone, FromRow)]
pub struct PriceWindowStats {
    pub exit_price: f64,
    pub max_price: f64,
    pub min_price: f64,
    pub resolved_at: DateTime<Utc>,
}

impl Repository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn candidate_consensus(
        &self,
        min_score: f64,
        min_confirmations: usize,
        signal_window_secs: i64,
    ) -> Result<Vec<SignalCandidate>> {
        let rows = sqlx::query_as::<_, SignalCandidate>(
            r#"
            WITH latest_scores AS (
                SELECT DISTINCT ON (trader_id) trader_id, total_score
                FROM trader_scores
                WHERE timestamp > NOW() - INTERVAL '15 minutes'
                  AND EXISTS (
                        SELECT 1
                        FROM trader_discovery_rankings r
                        WHERE r.trader_id = trader_scores.trader_id
                          AND r.promoted = TRUE
                  )
                ORDER BY trader_id, timestamp DESC
            ),
            recent_entries AS (
                SELECT DISTINCT ON (p.trader_id, p.coin, p.direction,
                    TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $3) * $3))
                    p.trader_id,
                    p.coin,
                    p.direction,
                    p.entry_price,
                    p.timestamp AS entry_timestamp,
                    TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $3) * $3) AS window_start
                FROM positions p
                WHERE p.timestamp > NOW() - INTERVAL '30 minutes'
                ORDER BY p.trader_id, p.coin, p.direction,
                         TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $3) * $3),
                         p.timestamp ASC
            ),
            grouped AS (
                SELECT
                    e.coin,
                    e.direction,
                    COUNT(DISTINCT e.trader_id) AS supporting_traders,
                    AVG(ls.total_score) AS avg_score,
                    SUM(ls.total_score / 100.0) AS quality_weight,
                    MAX(e.entry_timestamp) AS latest_entry_timestamp,
                    e.window_start,
                    e.window_start + ($3 * INTERVAL '1 second') AS window_end
                FROM recent_entries e
                JOIN latest_scores ls ON ls.trader_id = e.trader_id
                WHERE ls.total_score >= $1
                GROUP BY e.coin, e.direction, e.window_start
                HAVING COUNT(DISTINCT e.trader_id) >= $2
            )
            SELECT
                g.coin,
                g.direction,
                g.supporting_traders,
                g.avg_score,
                g.quality_weight,
                COALESCE(latest.funding_rate, 0.0) AS funding_rate,
                COALESCE(latest.open_interest, 0.0) AS open_interest,
                COALESCE(latest.price, 0.0) AS reference_price,
                COALESCE(((latest.price / NULLIF(price_5m.price, 0.0)) - 1.0) * 10000.0, 0.0) AS momentum_5m_bps,
                COALESCE(((latest.price / NULLIF(price_15m.price, 0.0)) - 1.0) * 10000.0, 0.0) AS momentum_15m_bps,
                COALESCE(vol.volatility_1h_bps, 0.0) AS volatility_1h_bps,
                g.latest_entry_timestamp,
                g.window_start,
                g.window_end
            FROM grouped g
            LEFT JOIN LATERAL (
                SELECT m.price, m.funding_rate, m.open_interest, m.timestamp
                FROM market_snapshots m
                WHERE m.coin = g.coin
                ORDER BY ABS(EXTRACT(EPOCH FROM (m.timestamp - g.latest_entry_timestamp)))
                LIMIT 1
            ) latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT m.price
                FROM market_snapshots m
                WHERE m.coin = g.coin
                  AND m.timestamp <= g.latest_entry_timestamp - INTERVAL '5 minutes'
                ORDER BY m.timestamp DESC
                LIMIT 1
            ) price_5m ON TRUE
            LEFT JOIN LATERAL (
                SELECT m.price
                FROM market_snapshots m
                WHERE m.coin = g.coin
                  AND m.timestamp <= g.latest_entry_timestamp - INTERVAL '15 minutes'
                ORDER BY m.timestamp DESC
                LIMIT 1
            ) price_15m ON TRUE
            LEFT JOIN LATERAL (
                SELECT STDDEV_SAMP(ret.ret_bps) AS volatility_1h_bps
                FROM (
                    SELECT
                        CASE
                            WHEN LAG(m.price) OVER (ORDER BY m.timestamp) > 0
                            THEN ((m.price / LAG(m.price) OVER (ORDER BY m.timestamp)) - 1.0) * 10000.0
                            ELSE NULL
                        END AS ret_bps
                    FROM market_snapshots m
                    WHERE m.coin = g.coin
                      AND m.timestamp BETWEEN g.latest_entry_timestamp - INTERVAL '1 hour' AND g.latest_entry_timestamp
                ) ret
            ) vol ON TRUE
            ORDER BY g.quality_weight DESC, g.supporting_traders DESC, g.latest_entry_timestamp DESC
            "#,
        )
        .bind(min_score)
        .bind(min_confirmations as i64)
        .bind(signal_window_secs)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn candidate_contributors(
        &self,
        candidate: &SignalCandidate,
        min_score: f64,
        signal_window_secs: i64,
    ) -> Result<Vec<SignalContributorCandidate>> {
        let rows = sqlx::query_as::<_, SignalContributorCandidate>(
            r#"
            WITH latest_scores AS (
                SELECT DISTINCT ON (trader_id) trader_id, total_score
                FROM trader_scores
                WHERE timestamp > NOW() - INTERVAL '15 minutes'
                  AND EXISTS (
                        SELECT 1
                        FROM trader_discovery_rankings r
                        WHERE r.trader_id = trader_scores.trader_id
                          AND r.promoted = TRUE
                  )
                ORDER BY trader_id, timestamp DESC
            ),
            recent_entries AS (
                SELECT DISTINCT ON (p.trader_id, p.coin, p.direction,
                    TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $5) * $5))
                    p.trader_id,
                    t.wallet,
                    ls.total_score AS trader_score,
                    ls.total_score / 100.0 AS weight,
                    p.entry_price,
                    p.timestamp AS entry_timestamp,
                    TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $5) * $5) AS window_start
                FROM positions p
                JOIN traders t ON t.id = p.trader_id
                JOIN latest_scores ls ON ls.trader_id = p.trader_id
                WHERE p.timestamp > NOW() - INTERVAL '30 minutes'
                  AND ls.total_score >= $1
                  AND p.coin = $2
                  AND p.direction = $3
                ORDER BY p.trader_id, p.coin, p.direction,
                         TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM p.timestamp) / $5) * $5),
                         p.timestamp ASC
            )
            SELECT trader_id, wallet, trader_score, weight, entry_price, entry_timestamp
            FROM recent_entries
            WHERE window_start = $4
            ORDER BY trader_score DESC, entry_timestamp ASC
            "#,
        )
        .bind(min_score)
        .bind(&candidate.coin)
        .bind(&candidate.direction)
        .bind(candidate.window_start)
        .bind(signal_window_secs)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn upsert_signal(
        &self,
        signal: &Signal,
        contributors: &[SignalContributorCandidate],
    ) -> Result<Uuid> {
        let mut tx = self.pool.begin().await?;
        let signal_id = sqlx::query_scalar::<_, Uuid>(
            r#"
            INSERT INTO signals (
                id, coin, direction, score, confidence_score, supporting_traders, quality_weight,
                momentum_score, volatility_score, regime, market_context, rationale, status,
                consensus_window_key, signal_window_start, signal_window_end, activated_at,
                expires_at, resolved_at, reference_price, timestamp
            ) VALUES (
                $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21
            )
            ON CONFLICT (consensus_window_key) DO UPDATE SET
                score = EXCLUDED.score,
                confidence_score = EXCLUDED.confidence_score,
                supporting_traders = EXCLUDED.supporting_traders,
                quality_weight = EXCLUDED.quality_weight,
                momentum_score = EXCLUDED.momentum_score,
                volatility_score = EXCLUDED.volatility_score,
                regime = EXCLUDED.regime,
                market_context = EXCLUDED.market_context,
                rationale = EXCLUDED.rationale,
                status = EXCLUDED.status,
                signal_window_start = EXCLUDED.signal_window_start,
                signal_window_end = EXCLUDED.signal_window_end,
                activated_at = EXCLUDED.activated_at,
                expires_at = EXCLUDED.expires_at,
                resolved_at = EXCLUDED.resolved_at,
                reference_price = EXCLUDED.reference_price,
                timestamp = EXCLUDED.timestamp
            RETURNING id
            "#,
        )
        .bind(signal.id)
        .bind(&signal.coin)
        .bind(&signal.direction)
        .bind(signal.score)
        .bind(signal.confidence_score)
        .bind(signal.supporting_traders)
        .bind(signal.quality_weight)
        .bind(signal.momentum_score)
        .bind(signal.volatility_score)
        .bind(&signal.regime)
        .bind(&signal.market_context)
        .bind(&signal.rationale)
        .bind(&signal.status)
        .bind(&signal.consensus_window_key)
        .bind(signal.signal_window_start)
        .bind(signal.signal_window_end)
        .bind(signal.activated_at)
        .bind(signal.expires_at)
        .bind(signal.resolved_at)
        .bind(signal.reference_price)
        .bind(signal.timestamp)
        .fetch_one(&mut *tx)
        .await?;

        sqlx::query("DELETE FROM signal_contributors WHERE signal_id = $1")
            .bind(signal_id)
            .execute(&mut *tx)
            .await?;

        for contributor in contributors {
            sqlx::query(
                "INSERT INTO signal_contributors (
                    id, signal_id, trader_id, wallet, trader_score, weight, entry_price, entry_timestamp
                 ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            )
            .bind(Uuid::new_v4())
            .bind(signal_id)
            .bind(contributor.trader_id)
            .bind(&contributor.wallet)
            .bind(contributor.trader_score)
            .bind(contributor.weight)
            .bind(contributor.entry_price)
            .bind(contributor.entry_timestamp)
            .execute(&mut *tx)
            .await?;
        }

        tx.commit().await?;
        Ok(signal_id)
    }

    pub async fn mark_expired_signals(&self) -> Result<u64> {
        let result = sqlx::query(
            "UPDATE signals
             SET status = 'expired', resolved_at = COALESCE(resolved_at, NOW())
             WHERE status = 'active'
               AND expires_at <= NOW()",
        )
        .execute(&self.pool)
        .await?;
        Ok(result.rows_affected())
    }

    pub async fn due_outcomes(&self, horizon_minutes: i32) -> Result<Vec<DueOutcomeCandidate>> {
        let rows = sqlx::query_as::<_, DueOutcomeCandidate>(
            r#"
            SELECT s.id AS signal_id, s.coin, s.direction, s.reference_price, s.signal_window_end
            FROM signals s
            LEFT JOIN signal_outcomes o
              ON o.signal_id = s.id
             AND o.horizon_minutes = $1
            WHERE o.signal_id IS NULL
              AND s.signal_window_end <= NOW() - ($1 * INTERVAL '1 minute')
            ORDER BY s.signal_window_end ASC
            "#,
        )
        .bind(horizon_minutes)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn price_window_stats(
        &self,
        coin: &str,
        start: DateTime<Utc>,
        horizon_minutes: i32,
    ) -> Result<Option<PriceWindowStats>> {
        let end = start + Duration::minutes(horizon_minutes as i64);
        let stats = sqlx::query_as::<_, PriceWindowStats>(
            r#"
            SELECT
                COALESCE(exit_point.price, window_stats.max_price) AS exit_price,
                window_stats.max_price,
                window_stats.min_price,
                COALESCE(exit_point.timestamp, $3) AS resolved_at
            FROM (
                SELECT MAX(price) AS max_price, MIN(price) AS min_price
                FROM market_snapshots
                WHERE coin = $1
                  AND timestamp BETWEEN $2 AND $3
            ) window_stats
            LEFT JOIN LATERAL (
                SELECT price, timestamp
                FROM market_snapshots
                WHERE coin = $1
                  AND timestamp >= $3
                ORDER BY timestamp ASC
                LIMIT 1
            ) exit_point ON TRUE
            "#,
        )
        .bind(coin)
        .bind(start)
        .bind(end)
        .fetch_optional(&self.pool)
        .await?;
        Ok(stats)
    }

    pub async fn insert_signal_outcome(
        &self,
        outcome: &SignalOutcome,
        resolve_signal: bool,
    ) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO signal_outcomes (
                id, signal_id, horizon_minutes, entry_price, exit_price, realized_return_bps,
                max_favorable_excursion_bps, max_adverse_excursion_bps, outcome_label, resolved_at
             ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
             ON CONFLICT (signal_id, horizon_minutes) DO NOTHING",
        )
        .bind(outcome.id)
        .bind(outcome.signal_id)
        .bind(outcome.horizon_minutes)
        .bind(outcome.entry_price)
        .bind(outcome.exit_price)
        .bind(outcome.realized_return_bps)
        .bind(outcome.max_favorable_excursion_bps)
        .bind(outcome.max_adverse_excursion_bps)
        .bind(&outcome.outcome_label)
        .bind(outcome.resolved_at)
        .execute(&mut *tx)
        .await?;

        if resolve_signal {
            sqlx::query("UPDATE signals SET status = 'resolved', resolved_at = $2 WHERE id = $1")
                .bind(outcome.signal_id)
                .bind(outcome.resolved_at)
                .execute(&mut *tx)
                .await?;
        }

        tx.commit().await?;
        Ok(())
    }

    pub async fn list_signals(&self, query: &SignalQuery) -> Result<Vec<Signal>> {
        let limit = query.pagination.limit.clamp(1, 100);
        let offset = (query.pagination.page.max(1) - 1) * limit;
        let rows = sqlx::query_as::<_, Signal>(
            r#"
            SELECT id, coin, direction, score, confidence_score, supporting_traders, quality_weight,
                   momentum_score, volatility_score, regime, market_context, rationale, status,
                   consensus_window_key, signal_window_start, signal_window_end, activated_at,
                   expires_at, resolved_at, reference_price, timestamp, created_at
            FROM signals
            WHERE ($1::TEXT IS NULL OR coin = $1)
              AND ($2::DOUBLE PRECISION IS NULL OR confidence_score >= $2)
              AND ($3::TEXT IS NULL OR status = $3)
            ORDER BY confidence_score DESC, timestamp DESC
            LIMIT $4 OFFSET $5
            "#,
        )
        .bind(query.coin.clone())
        .bind(query.min_score)
        .bind(query.status.clone())
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }
}
