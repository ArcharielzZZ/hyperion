use anyhow::Result;
use chrono::{DateTime, Utc};
use hyperion_models::{PaperTrade, Signal, SimulationBatch, SimulationReport, SimulationRun};
use hyperion_schemas::{
    SimulationComparisonResponse, SimulationReportResponse, SimulationRunSummaryResponse,
    TradeJournalResponse,
};
use sqlx::{FromRow, PgPool};
use std::collections::HashMap;

#[derive(Clone)]
pub struct Repository {
    pool: PgPool,
}

#[derive(Debug, Clone, FromRow)]
pub struct PricePoint {
    pub price: f64,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone)]
pub struct PersistedStrategyRun {
    pub run: SimulationRun,
    pub report: SimulationReport,
    pub trades: Vec<PaperTrade>,
}

impl Repository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    pub async fn replay_signals(&self, replay_days: i64) -> Result<Vec<Signal>> {
        let rows = sqlx::query_as::<_, Signal>(
            "SELECT id, coin, direction, score, confidence_score, supporting_traders, quality_weight,
                    momentum_score, volatility_score, regime, market_context, rationale, status,
                    consensus_window_key, signal_window_start, signal_window_end, activated_at,
                    expires_at, resolved_at, reference_price, timestamp, created_at
             FROM signals
             WHERE timestamp > NOW() - ($1 * INTERVAL '1 day')
               AND status <> 'expired'
             ORDER BY timestamp ASC",
        )
        .bind(replay_days)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn market_price_after(
        &self,
        coin: &str,
        ts: DateTime<Utc>,
    ) -> Result<Option<PricePoint>> {
        let point = sqlx::query_as::<_, PricePoint>(
            "SELECT price, timestamp
             FROM market_snapshots
             WHERE coin = $1 AND timestamp >= $2
             ORDER BY timestamp ASC
             LIMIT 1",
        )
        .bind(coin)
        .bind(ts)
        .fetch_optional(&self.pool)
        .await?;
        Ok(point)
    }

    pub async fn market_window(
        &self,
        coin: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<Vec<PricePoint>> {
        let rows = sqlx::query_as::<_, PricePoint>(
            "SELECT price, timestamp
             FROM market_snapshots
             WHERE coin = $1
               AND timestamp BETWEEN $2 AND $3
             ORDER BY timestamp ASC",
        )
        .bind(coin)
        .bind(start)
        .bind(end)
        .fetch_all(&self.pool)
        .await?;
        Ok(rows)
    }

    pub async fn save_batch(
        &self,
        batch: &SimulationBatch,
        runs: &[PersistedStrategyRun],
    ) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("INSERT INTO simulation_batches (id, name, dataset_start, dataset_end) VALUES ($1,$2,$3,$4)")
            .bind(batch.id)
            .bind(&batch.name)
            .bind(batch.dataset_start)
            .bind(batch.dataset_end)
            .execute(&mut *tx)
            .await?;

        for strategy_run in runs {
            sqlx::query(
                "INSERT INTO simulation_runs (
                    id, batch_id, strategy_key, strategy_name, dataset_start, dataset_end,
                    starting_equity, ending_equity, total_return_pct, max_drawdown_pct, win_rate_pct,
                    trade_count, sharpe_like, expectancy, profit_factor, avg_trade_return_pct, config_json
                 ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)",
            )
            .bind(strategy_run.run.id)
            .bind(strategy_run.run.batch_id)
            .bind(&strategy_run.run.strategy_key)
            .bind(&strategy_run.run.strategy_name)
            .bind(strategy_run.run.dataset_start)
            .bind(strategy_run.run.dataset_end)
            .bind(strategy_run.run.starting_equity)
            .bind(strategy_run.run.ending_equity)
            .bind(strategy_run.run.total_return_pct)
            .bind(strategy_run.run.max_drawdown_pct)
            .bind(strategy_run.run.win_rate_pct)
            .bind(strategy_run.run.trade_count)
            .bind(strategy_run.run.sharpe_like)
            .bind(strategy_run.run.expectancy)
            .bind(strategy_run.run.profit_factor)
            .bind(strategy_run.run.avg_trade_return_pct)
            .bind(&strategy_run.run.config_json)
            .execute(&mut *tx)
            .await?;

            sqlx::query(
                "INSERT INTO simulation_reports (
                    id, run_id, total_fees_paid, total_slippage_paid, avg_win_pct, avg_loss_pct,
                    payoff_ratio, return_volatility_pct, peak_equity, trough_equity,
                    max_consecutive_wins, max_consecutive_losses, drawdown_curve_json, regime_breakdown_json
                 ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)",
            )
            .bind(strategy_run.report.id)
            .bind(strategy_run.report.run_id)
            .bind(strategy_run.report.total_fees_paid)
            .bind(strategy_run.report.total_slippage_paid)
            .bind(strategy_run.report.avg_win_pct)
            .bind(strategy_run.report.avg_loss_pct)
            .bind(strategy_run.report.payoff_ratio)
            .bind(strategy_run.report.return_volatility_pct)
            .bind(strategy_run.report.peak_equity)
            .bind(strategy_run.report.trough_equity)
            .bind(strategy_run.report.max_consecutive_wins)
            .bind(strategy_run.report.max_consecutive_losses)
            .bind(&strategy_run.report.drawdown_curve_json)
            .bind(&strategy_run.report.regime_breakdown_json)
            .execute(&mut *tx)
            .await?;

            for trade in &strategy_run.trades {
                sqlx::query(
                    "INSERT INTO paper_trades (
                        id, run_id, signal_id, strategy_key, coin, direction, entry_price, exit_price,
                        size, fee_paid, slippage_paid, pnl, entry_reason, exit_reason, regime,
                        entry_fee_paid, exit_fee_paid, entry_slippage_bps, exit_slippage_bps,
                        return_pct, max_favorable_excursion_pct, max_adverse_excursion_pct,
                        equity_before, equity_after, risk_fraction, holding_minutes, confidence_score,
                        opened_at, closed_at
                     ) VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,
                        $21,$22,$23,$24,$25,$26,$27,$28,$29
                     )",
                )
                .bind(trade.id)
                .bind(trade.run_id)
                .bind(trade.signal_id)
                .bind(&trade.strategy_key)
                .bind(&trade.coin)
                .bind(&trade.direction)
                .bind(trade.entry_price)
                .bind(trade.exit_price)
                .bind(trade.size)
                .bind(trade.fee_paid)
                .bind(trade.slippage_paid)
                .bind(trade.pnl)
                .bind(&trade.entry_reason)
                .bind(&trade.exit_reason)
                .bind(&trade.regime)
                .bind(trade.entry_fee_paid)
                .bind(trade.exit_fee_paid)
                .bind(trade.entry_slippage_bps)
                .bind(trade.exit_slippage_bps)
                .bind(trade.return_pct)
                .bind(trade.max_favorable_excursion_pct)
                .bind(trade.max_adverse_excursion_pct)
                .bind(trade.equity_before)
                .bind(trade.equity_after)
                .bind(trade.risk_fraction)
                .bind(trade.holding_minutes)
                .bind(trade.confidence_score)
                .bind(trade.opened_at)
                .bind(trade.closed_at)
                .execute(&mut *tx)
                .await?;
            }
        }

        tx.commit().await?;
        Ok(())
    }

    pub async fn latest_comparison(&self) -> Result<Option<SimulationComparisonResponse>> {
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

        self.comparison_for_batch(batch.id).await
    }

    pub async fn comparison_for_batch(
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

        let report_rows = if runs.is_empty() {
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

        let report_map = report_rows
            .into_iter()
            .map(|report| (report.run_id, report))
            .collect::<HashMap<_, _>>();

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

    pub async fn run_report(&self, run_id: uuid::Uuid) -> Result<Option<SimulationReportResponse>> {
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
}
