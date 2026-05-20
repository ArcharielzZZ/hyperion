use anyhow::Result;
use chrono::{DateTime, Utc};
use hyperion_models::{
    BehavioralAlert, PaperTrade, Signal, SignalContributor, SignalOutcome, SimulationBatch,
    SimulationReport, SimulationRun, Trader, TraderBehaviorProfile, TraderDiscoveryRank,
    TraderScore,
};
use hyperion_schemas::{
    BehavioralDriftAnalysis, CopyabilityAdvisorySnapshot, DiscoveryRankingQuery, EntryTimingAnalysis,
    HoldDurationAnalysis, LeaderboardItem, LeaderboardQuery, RollingPnlAnalytics,
    SignalOutcomesResponse, SignalQuery, SignalSummaryResponse, SimulationComparisonResponse,
    SimulationReportResponse, SimulationRunSummaryResponse, TimeSeriesPoint, TradeJournalResponse,
    TraderBehaviorDetailResponse, TraderLifecycleMetrics,
};
use sqlx::{FromRow, PgPool};
use std::collections::HashMap;

#[derive(Debug, Clone, FromRow)]
struct CopyabilitySnapshotRow {
    trader_id: uuid::Uuid,
    copyability_score: f64,
    signal_frequency_component: f64,
    timing_component: f64,
    realized_ratio_component: f64,
    stability_component: f64,
    computed_at: DateTime<Utc>,
}

impl From<CopyabilitySnapshotRow> for CopyabilityAdvisorySnapshot {
    fn from(row: CopyabilitySnapshotRow) -> Self {
        Self {
            copyability_score: row.copyability_score,
            signal_frequency_component: row.signal_frequency_component,
            timing_component: row.timing_component,
            realized_ratio_component: row.realized_ratio_component,
            stability_component: row.stability_component,
            computed_at: row.computed_at,
        }
    }
}

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
}

#[derive(Debug, Clone, FromRow)]
struct HistoryRow {
    timestamp: DateTime<Utc>,
    value: f64,
}

impl Repository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn leaderboard(&self, query: &LeaderboardQuery) -> Result<Vec<LeaderboardItem>> {
        let limit = query.pagination.limit.clamp(1, 100);
        let offset = (query.pagination.page.max(1) - 1) * limit;
        let rows = sqlx::query_as::<_, LeaderboardItem>(
            r#"
            SELECT t.wallet, ts.total_score, ts.style, t.last_seen
            FROM traders t
            JOIN trader_discovery_rankings r
              ON r.trader_id = t.id
             AND r.promoted = TRUE
            JOIN LATERAL (
                SELECT total_score, style
                FROM trader_scores
                WHERE trader_id = t.id
                  AND timestamp > NOW() - INTERVAL '15 minutes'
                ORDER BY timestamp DESC
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

    pub async fn discovery_rankings(
        &self,
        query: &DiscoveryRankingQuery,
    ) -> Result<Vec<TraderDiscoveryRank>> {
        let limit = query.pagination.limit.clamp(1, 100);
        let offset = (query.pagination.page.max(1) - 1) * limit;
        let rows = sqlx::query_as::<_, TraderDiscoveryRank>(
            r#"
            SELECT id, trader_id, wallet, public_trade_count_1h, public_trade_count_24h,
                   public_notional_usd_1h, public_notional_usd_24h, active_coins_24h, buy_ratio_24h,
                   fills_24h, positions_24h, pnl_snapshots_24h, last_public_trade_at,
                   latest_behavior_score, data_coverage_score, activity_score, discovery_score,
                   rank_tier, promoted, timestamp, created_at
            FROM trader_discovery_rankings
            WHERE ($1::DOUBLE PRECISION IS NULL OR discovery_score >= $1)
              AND ($2::TEXT IS NULL OR rank_tier = $2)
              AND ($3::BOOLEAN IS NULL OR promoted = $3)
            ORDER BY discovery_score DESC, last_public_trade_at DESC NULLS LAST, timestamp DESC
            LIMIT $4 OFFSET $5
            "#,
        )
        .bind(query.min_score)
        .bind(query.tier.clone())
        .bind(query.promoted_only)
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn list_signals(
        &self,
        query: &SignalQuery,
        forced_status: Option<&str>,
    ) -> Result<Vec<SignalSummaryResponse>> {
        let signals = self.fetch_signals(query, forced_status).await?;
        self.attach_signal_contributors(signals).await
    }

    async fn fetch_signals(
        &self,
        query: &SignalQuery,
        forced_status: Option<&str>,
    ) -> Result<Vec<Signal>> {
        let limit = query.pagination.limit.clamp(1, 100);
        let offset = (query.pagination.page.max(1) - 1) * limit;
        let forced_status = forced_status.map(str::to_string);
        let rows = sqlx::query_as::<_, Signal>(
            "SELECT id, coin, direction, score, confidence_score, supporting_traders, quality_weight,
                    momentum_score, volatility_score, regime, market_context, rationale, status,
                    consensus_window_key, signal_window_start, signal_window_end, activated_at,
                    expires_at, resolved_at, reference_price, timestamp, created_at
             FROM signals
             WHERE ($1::TEXT IS NULL OR coin = $1)
               AND ($2::DOUBLE PRECISION IS NULL OR confidence_score >= $2)
               AND (
                    COALESCE($3::TEXT, $4::TEXT) IS NULL
                    OR (COALESCE($3::TEXT, $4::TEXT) = '__historical__' AND status <> 'active')
                    OR status = COALESCE($3::TEXT, $4::TEXT)
               )
             ORDER BY confidence_score DESC, timestamp DESC
             LIMIT $5 OFFSET $6",
        )
        .bind(query.coin.clone())
        .bind(query.min_score)
        .bind(query.status.clone())
        .bind(forced_status)
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn latest_scores(&self, limit: i64) -> Result<Vec<TraderScore>> {
        let rows = sqlx::query_as::<_, TraderScore>(
            r#"
            SELECT DISTINCT ON (trader_id)
                id, trader_id, consistency_score, survivability_score, timing_score,
                leverage_discipline_score, conviction_score, total_score, style, timestamp, created_at
            FROM trader_scores
            WHERE timestamp > NOW() - INTERVAL '15 minutes'
              AND EXISTS (
                    SELECT 1
                    FROM trader_discovery_rankings r
                    WHERE r.trader_id = trader_scores.trader_id
                      AND r.promoted = TRUE
              )
            ORDER BY trader_id, timestamp DESC
            LIMIT $1
            "#,
        )
        .bind(limit.clamp(1, 200))
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn latest_copyability_snapshot_for_trader(
        &self,
        trader_id: uuid::Uuid,
    ) -> Result<Option<CopyabilityAdvisorySnapshot>> {
        let row = sqlx::query_as::<_, CopyabilitySnapshotRow>(
            r#"
            SELECT trader_id,
                   copyability_score,
                   signal_frequency_component,
                   timing_component,
                   realized_ratio_component,
                   stability_component,
                   computed_at
            FROM trader_copyability_advisory
            WHERE trader_id = $1
            ORDER BY computed_at DESC
            LIMIT 1
            "#,
        )
        .bind(trader_id)
        .fetch_optional(&self.pool)
        .await?;
        Ok(row.map(Into::into))
    }

    pub async fn latest_copyability_snapshots_for_traders(
        &self,
        trader_ids: &[uuid::Uuid],
    ) -> Result<HashMap<uuid::Uuid, CopyabilityAdvisorySnapshot>> {
        if trader_ids.is_empty() {
            return Ok(HashMap::new());
        }
        let rows = sqlx::query_as::<_, CopyabilitySnapshotRow>(
            r#"
            SELECT DISTINCT ON (ca.trader_id)
                ca.trader_id,
                ca.copyability_score,
                ca.signal_frequency_component,
                ca.timing_component,
                ca.realized_ratio_component,
                ca.stability_component,
                ca.computed_at
            FROM trader_copyability_advisory ca
            WHERE ca.trader_id = ANY($1)
            ORDER BY ca.trader_id, ca.computed_at DESC
            "#,
        )
        .bind(trader_ids)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows
            .into_iter()
            .map(|row| (row.trader_id, CopyabilityAdvisorySnapshot::from(row)))
            .collect())
    }

    pub async fn latest_simulation(&self) -> Result<Option<SimulationComparisonResponse>> {
        let batch = sqlx::query_as::<_, SimulationBatch>(
            "SELECT id, name, dataset_start, dataset_end, created_at
             FROM simulation_batches
             ORDER BY created_at DESC
             LIMIT 1",
        )
        .fetch_optional(&self.pool)
        .await?;

        let Some(batch) = batch else {
            return Ok(None);
        };

        self.simulation_comparison(batch.id).await
    }

    pub async fn simulation_comparison(
        &self,
        batch_id: uuid::Uuid,
    ) -> Result<Option<SimulationComparisonResponse>> {
        let batch = sqlx::query_as::<_, SimulationBatch>(
            "SELECT id, name, dataset_start, dataset_end, created_at
             FROM simulation_batches
             WHERE id = $1",
        )
        .bind(batch_id)
        .fetch_optional(&self.pool)
        .await?;

        let Some(batch) = batch else {
            return Ok(None);
        };

        let runs = sqlx::query_as::<_, SimulationRun>(
            "SELECT id, batch_id, strategy_key, strategy_name, dataset_start, dataset_end,
                    starting_equity, ending_equity, total_return_pct, max_drawdown_pct, win_rate_pct,
                    trade_count, sharpe_like, expectancy, profit_factor, avg_trade_return_pct,
                    config_json, created_at
             FROM simulation_runs
             WHERE batch_id = $1
             ORDER BY sharpe_like DESC, total_return_pct DESC",
        )
        .bind(batch_id)
        .fetch_all(&self.pool)
        .await?;

        let reports = if runs.is_empty() {
            Vec::new()
        } else {
            let run_ids = runs.iter().map(|run| run.id).collect::<Vec<_>>();
            sqlx::query_as::<_, SimulationReport>(
                "SELECT id, run_id, total_fees_paid, total_slippage_paid, avg_win_pct, avg_loss_pct,
                        payoff_ratio, return_volatility_pct, peak_equity, trough_equity,
                        max_consecutive_wins, max_consecutive_losses, drawdown_curve_json,
                        regime_breakdown_json, created_at
                 FROM simulation_reports
                 WHERE run_id = ANY($1)",
            )
            .bind(&run_ids)
            .fetch_all(&self.pool)
            .await?
        };

        let report_map = reports
            .into_iter()
            .map(|report| (report.run_id, report))
            .collect::<std::collections::HashMap<_, _>>();

        Ok(Some(SimulationComparisonResponse {
            batch,
            runs: runs
                .into_iter()
                .map(|run| SimulationRunSummaryResponse {
                    report: report_map.get(&run.id).cloned(),
                    run,
                })
                .collect(),
        }))
    }

    pub async fn simulation_report(
        &self,
        run_id: uuid::Uuid,
    ) -> Result<Option<SimulationReportResponse>> {
        let run = sqlx::query_as::<_, SimulationRun>(
            "SELECT id, batch_id, strategy_key, strategy_name, dataset_start, dataset_end,
                    starting_equity, ending_equity, total_return_pct, max_drawdown_pct, win_rate_pct,
                    trade_count, sharpe_like, expectancy, profit_factor, avg_trade_return_pct,
                    config_json, created_at
             FROM simulation_runs
             WHERE id = $1",
        )
        .bind(run_id)
        .fetch_optional(&self.pool)
        .await?;

        let Some(run) = run else {
            return Ok(None);
        };

        let batch = sqlx::query_as::<_, SimulationBatch>(
            "SELECT id, name, dataset_start, dataset_end, created_at
             FROM simulation_batches
             WHERE id = $1",
        )
        .bind(run.batch_id)
        .fetch_one(&self.pool)
        .await?;

        let report = sqlx::query_as::<_, SimulationReport>(
            "SELECT id, run_id, total_fees_paid, total_slippage_paid, avg_win_pct, avg_loss_pct,
                    payoff_ratio, return_volatility_pct, peak_equity, trough_equity,
                    max_consecutive_wins, max_consecutive_losses, drawdown_curve_json,
                    regime_breakdown_json, created_at
             FROM simulation_reports
             WHERE run_id = $1",
        )
        .bind(run_id)
        .fetch_one(&self.pool)
        .await?;

        Ok(Some(SimulationReportResponse { batch, run, report }))
    }

    pub async fn trade_journal(
        &self,
        run_id: uuid::Uuid,
        page: i64,
        limit: i64,
    ) -> Result<Option<TradeJournalResponse>> {
        let run = sqlx::query_as::<_, SimulationRun>(
            "SELECT id, batch_id, strategy_key, strategy_name, dataset_start, dataset_end,
                    starting_equity, ending_equity, total_return_pct, max_drawdown_pct, win_rate_pct,
                    trade_count, sharpe_like, expectancy, profit_factor, avg_trade_return_pct,
                    config_json, created_at
             FROM simulation_runs
             WHERE id = $1",
        )
        .bind(run_id)
        .fetch_optional(&self.pool)
        .await?;

        let Some(run) = run else {
            return Ok(None);
        };

        let page = page.max(1);
        let limit = limit.clamp(1, 250);
        let offset = (page - 1) * limit;
        let trades = sqlx::query_as::<_, PaperTrade>(
            "SELECT id, run_id, signal_id, strategy_key, coin, direction, entry_price, exit_price,
                    size, fee_paid, slippage_paid, pnl, entry_reason, exit_reason, regime,
                    entry_fee_paid, exit_fee_paid, entry_slippage_bps, exit_slippage_bps,
                    return_pct, max_favorable_excursion_pct, max_adverse_excursion_pct,
                    equity_before, equity_after, risk_fraction, holding_minutes, confidence_score,
                    opened_at, closed_at, created_at
             FROM paper_trades
             WHERE run_id = $1
             ORDER BY opened_at ASC
             LIMIT $2 OFFSET $3",
        )
        .bind(run_id)
        .bind(limit)
        .bind(offset)
        .fetch_all(&self.pool)
        .await?;

        Ok(Some(TradeJournalResponse { run, trades }))
    }

    pub async fn signal_outcomes(
        &self,
        signal_id: uuid::Uuid,
    ) -> Result<Option<SignalOutcomesResponse>> {
        let signal = sqlx::query_as::<_, Signal>(
            "SELECT id, coin, direction, score, confidence_score, supporting_traders, quality_weight,
                    momentum_score, volatility_score, regime, market_context, rationale, status,
                    consensus_window_key, signal_window_start, signal_window_end, activated_at,
                    expires_at, resolved_at, reference_price, timestamp, created_at
             FROM signals
             WHERE id = $1",
        )
        .bind(signal_id)
        .fetch_optional(&self.pool)
        .await?;

        let Some(signal) = signal else {
            return Ok(None);
        };

        let outcomes = sqlx::query_as::<_, SignalOutcome>(
            "SELECT id, signal_id, horizon_minutes, entry_price, exit_price, realized_return_bps,
                    max_favorable_excursion_bps, max_adverse_excursion_bps, outcome_label, resolved_at, created_at
             FROM signal_outcomes
             WHERE signal_id = $1
             ORDER BY horizon_minutes ASC",
        )
        .bind(signal_id)
        .fetch_all(&self.pool)
        .await?;

        Ok(Some(SignalOutcomesResponse { signal, outcomes }))
    }

    pub async fn trader_behavior_detail(
        &self,
        wallet: &str,
    ) -> Result<Option<TraderBehaviorDetailResponse>> {
        let trader = sqlx::query_as::<_, Trader>(
            "SELECT id, wallet, first_seen, last_seen, created_at FROM traders WHERE wallet = $1",
        )
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

        let copyability_advisory = self
            .latest_copyability_snapshot_for_trader(trader.id)
            .await?;

        let latest_behavior_profile = sqlx::query_as::<_, TraderBehaviorProfile>(
            r#"
            SELECT id, trader_id, rolling_consistency_score_7d, rolling_consistency_score_30d,
                   avg_daily_pnl_7d, avg_daily_pnl_30d, pnl_volatility_7d, pnl_volatility_30d,
                   win_rate_30d, recent_leverage_avg_7d, leverage_avg_30d, leverage_peak_30d,
                   leverage_volatility_30d, max_drawdown_pct_30d, avg_hold_duration_secs_30d,
                   median_hold_duration_secs_30d, entry_timing_edge_bps_30d, favorable_entry_rate_30d,
                   behavioral_drift_score, emotional_volatility_score, revenge_trading_score,
                   sizing_instability_score, consistency_score_delta, active_days_30d, fills_7d,
                   fills_30d, lifecycle_stage, window_start, window_end, timestamp, created_at
            FROM trader_behavior_profiles
            WHERE trader_id = $1
            ORDER BY timestamp DESC
            LIMIT 1
            "#,
        )
        .bind(trader.id)
        .fetch_optional(&self.pool)
        .await?;

        let leverage_history = sqlx::query_as::<_, HistoryRow>(
            r#"
            SELECT date_trunc('day', timestamp) AS timestamp, AVG(ABS(leverage)) AS value
            FROM positions
            WHERE trader_id = $1
              AND timestamp > NOW() - INTERVAL '30 days'
            GROUP BY 1
            ORDER BY 1 ASC
            "#,
        )
        .bind(trader.id)
        .fetch_all(&self.pool)
        .await?
        .into_iter()
        .map(|row| TimeSeriesPoint {
            timestamp: row.timestamp,
            value: row.value,
        })
        .collect::<Vec<_>>();

        let daily_equity_rows = sqlx::query_as::<_, HistoryRow>(
            r#"
            SELECT DISTINCT ON (date_trunc('day', timestamp))
                date_trunc('day', timestamp) AS timestamp,
                equity AS value
            FROM pnl_snapshots
            WHERE trader_id = $1
              AND timestamp > NOW() - INTERVAL '30 days'
            ORDER BY date_trunc('day', timestamp), timestamp DESC
            "#,
        )
        .bind(trader.id)
        .fetch_all(&self.pool)
        .await?;

        let drawdown_history = build_drawdown_history(daily_equity_rows.clone());

        let consistency_history = sqlx::query_as::<_, HistoryRow>(
            r#"
            SELECT timestamp, rolling_consistency_score_7d AS value
            FROM trader_behavior_profiles
            WHERE trader_id = $1
            ORDER BY timestamp DESC
            LIMIT 30
            "#,
        )
        .bind(trader.id)
        .fetch_all(&self.pool)
        .await?
        .into_iter()
        .rev()
        .map(|row| TimeSeriesPoint {
            timestamp: row.timestamp,
            value: row.value,
        })
        .collect::<Vec<_>>();

        let alerts = sqlx::query_as::<_, BehavioralAlert>(
            "SELECT id, trader_id, alert_type, severity, title, message, metric_value,
                    threshold_value, detected_at, dedupe_key, created_at
             FROM behavioral_alerts
             WHERE trader_id = $1
             ORDER BY detected_at DESC
             LIMIT 25",
        )
        .bind(trader.id)
        .fetch_all(&self.pool)
        .await?;

        let rolling_pnl_analytics =
            latest_behavior_profile
                .as_ref()
                .map(|profile| RollingPnlAnalytics {
                    avg_daily_pnl_7d: profile.avg_daily_pnl_7d,
                    avg_daily_pnl_30d: profile.avg_daily_pnl_30d,
                    pnl_volatility_7d: profile.pnl_volatility_7d,
                    pnl_volatility_30d: profile.pnl_volatility_30d,
                    win_rate_30d: profile.win_rate_30d,
                    equity_change_30d: compute_equity_change_30d_from_rows(&daily_equity_rows),
                });

        let hold_duration_analysis =
            latest_behavior_profile
                .as_ref()
                .map(|profile| HoldDurationAnalysis {
                    average_seconds: profile.avg_hold_duration_secs_30d,
                    median_seconds: profile.median_hold_duration_secs_30d,
                });

        let entry_timing_analysis =
            latest_behavior_profile
                .as_ref()
                .map(|profile| EntryTimingAnalysis {
                    average_edge_bps: profile.entry_timing_edge_bps_30d,
                    favorable_entry_rate: profile.favorable_entry_rate_30d,
                });

        let behavioral_drift =
            latest_behavior_profile
                .as_ref()
                .map(|profile| BehavioralDriftAnalysis {
                    drift_score: profile.behavioral_drift_score,
                    consistency_delta: profile.consistency_score_delta,
                    emotional_volatility_score: profile.emotional_volatility_score,
                    revenge_trading_score: profile.revenge_trading_score,
                    sizing_instability_score: profile.sizing_instability_score,
                });

        let lifecycle_metrics =
            latest_behavior_profile
                .as_ref()
                .map(|profile| TraderLifecycleMetrics {
                    first_seen: trader.first_seen,
                    last_seen: trader.last_seen,
                    age_days: (Utc::now() - trader.first_seen).num_days(),
                    days_since_last_seen: (Utc::now() - trader.last_seen).num_days(),
                    active_days_30d: profile.active_days_30d,
                    fills_7d: profile.fills_7d,
                    fills_30d: profile.fills_30d,
                    lifecycle_stage: profile.lifecycle_stage.clone(),
                });

        Ok(Some(TraderBehaviorDetailResponse {
            trader,
            latest_score,
            copyability_advisory,
            latest_behavior_profile,
            rolling_pnl_analytics,
            leverage_history,
            drawdown_history,
            consistency_history,
            hold_duration_analysis,
            entry_timing_analysis,
            behavioral_drift,
            lifecycle_metrics,
            alerts,
        }))
    }

    async fn attach_signal_contributors(
        &self,
        signals: Vec<Signal>,
    ) -> Result<Vec<SignalSummaryResponse>> {
        let signal_ids = signals.iter().map(|signal| signal.id).collect::<Vec<_>>();
        let contributors = if signal_ids.is_empty() {
            Vec::new()
        } else {
            sqlx::query_as::<_, SignalContributor>(
                "SELECT id, signal_id, trader_id, wallet, trader_score, weight, entry_price, entry_timestamp, created_at
                 FROM signal_contributors
                 WHERE signal_id = ANY($1)
                 ORDER BY trader_score DESC, entry_timestamp ASC",
            )
            .bind(&signal_ids)
            .fetch_all(&self.pool)
            .await?
        };

        let mut by_signal = std::collections::HashMap::<uuid::Uuid, Vec<SignalContributor>>::new();
        for contributor in contributors {
            by_signal
                .entry(contributor.signal_id)
                .or_default()
                .push(contributor);
        }

        Ok(signals
            .into_iter()
            .map(|signal| SignalSummaryResponse {
                contributors: by_signal.remove(&signal.id).unwrap_or_default(),
                signal,
            })
            .collect())
    }

    /// Single-row cross-table freshness and lag (see `v_data_freshness_now`).
    pub async fn data_freshness_live(&self) -> Result<serde_json::Value> {
        let row = sqlx::query_scalar::<_, serde_json::Value>(
            r#"SELECT to_jsonb(v) FROM v_data_freshness_now v"#,
        )
        .fetch_one(&self.pool)
        .await?;
        Ok(row)
    }

    /// Persist a point-in-time row from the live freshness view.
    pub async fn record_scanner_coverage_snapshot(&self) -> Result<i64> {
        let id = sqlx::query_scalar::<_, i64>(
            r#"INSERT INTO scanner_coverage_snapshots (payload)
               SELECT to_jsonb(v) FROM v_data_freshness_now v
               RETURNING id"#,
        )
        .fetch_one(&self.pool)
        .await?;
        Ok(id)
    }

    /// Newest-first stored scanner / freshness snapshots.
    pub async fn scanner_coverage_snapshots(&self, limit: i64) -> Result<Vec<serde_json::Value>> {
        let lim = limit.clamp(1, 500);
        let rows = sqlx::query_scalar::<_, serde_json::Value>(
            r#"SELECT jsonb_build_object(
                    'id', id,
                    'captured_at', captured_at,
                    'payload', payload
                )
                FROM scanner_coverage_snapshots
                ORDER BY id DESC
                LIMIT $1"#,
        )
        .bind(lim)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    /// Week 1 closure rollup (gap detector + snapshot cadence).
    pub async fn week1_health_summary(&self) -> Result<serde_json::Value> {
        let row = sqlx::query_scalar::<_, serde_json::Value>(
            r#"SELECT to_jsonb(v) FROM v_week1_health_summary v"#,
        )
        .fetch_one(&self.pool)
        .await?;
        Ok(row)
    }

    /// Per-channel lag status (`ok` / `warn` / `critical` / `missing`).
    pub async fn week1_channel_health(&self) -> Result<Vec<serde_json::Value>> {
        let rows = sqlx::query_scalar::<_, serde_json::Value>(
            r#"SELECT jsonb_build_object(
                    'channel', channel,
                    'max_ts', max_ts,
                    'lag_sec', lag_sec,
                    'status', status
                )
                FROM v_week1_data_channel_health
                ORDER BY CASE status
                    WHEN 'critical' THEN 1
                    WHEN 'warn' THEN 2
                    WHEN 'missing' THEN 3
                    ELSE 4
                END,
                channel"#,
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    /// Recent ingest websocket lifecycle events (newest first).
    pub async fn ingest_connection_events(&self, limit: i64) -> Result<Vec<serde_json::Value>> {
        let lim = limit.clamp(1, 1000);
        let rows = sqlx::query_scalar::<_, serde_json::Value>(
            r#"SELECT jsonb_build_object(
                    'id', id,
                    'occurred_at', occurred_at,
                    'event_type', event_type,
                    'detail', detail
                )
                FROM ingest_connection_events
                ORDER BY id DESC
                LIMIT $1"#,
        )
        .bind(lim)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }
}

fn build_drawdown_history(rows: Vec<HistoryRow>) -> Vec<TimeSeriesPoint> {
    let mut rows = rows;
    rows.sort_by_key(|row| row.timestamp);
    let mut peak: f64 = 0.0;
    rows.into_iter()
        .map(|row| {
            peak = peak.max(row.value);
            let drawdown = if peak > 0.0 {
                ((peak - row.value) / peak) * 100.0
            } else {
                0.0
            };
            TimeSeriesPoint {
                timestamp: row.timestamp,
                value: drawdown,
            }
        })
        .collect()
}

fn compute_equity_change_30d_from_rows(rows: &[HistoryRow]) -> f64 {
    if rows.len() < 2 {
        return 0.0;
    }
    let mut rows = rows.to_vec();
    rows.sort_by_key(|row| row.timestamp);
    rows.last().map(|last| last.value).unwrap_or(0.0)
        - rows.first().map(|first| first.value).unwrap_or(0.0)
}
