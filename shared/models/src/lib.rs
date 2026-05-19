use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Trader {
    pub id: Uuid,
    pub wallet: String,
    pub first_seen: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Fill {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub coin: String,
    pub side: String,
    pub size: f64,
    pub leverage: f64,
    pub price: f64,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Position {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub coin: String,
    pub direction: String,
    pub entry_price: f64,
    pub size: f64,
    pub leverage: f64,
    pub unrealized_pnl: f64,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct PnlSnapshot {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub equity: f64,
    pub realized_pnl: f64,
    pub unrealized_pnl: f64,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct MarketSnapshot {
    pub id: Uuid,
    pub coin: String,
    pub funding_rate: f64,
    pub open_interest: f64,
    pub price: f64,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct TraderScore {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub consistency_score: f64,
    pub survivability_score: f64,
    pub timing_score: f64,
    pub leverage_discipline_score: f64,
    pub conviction_score: f64,
    pub total_score: f64,
    pub style: String,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct TraderDiscoveryRank {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub wallet: String,
    pub public_trade_count_1h: i32,
    pub public_trade_count_24h: i32,
    pub public_notional_usd_1h: f64,
    pub public_notional_usd_24h: f64,
    pub active_coins_24h: i32,
    pub buy_ratio_24h: f64,
    pub fills_24h: i32,
    pub positions_24h: i32,
    pub pnl_snapshots_24h: i32,
    pub last_public_trade_at: Option<DateTime<Utc>>,
    pub latest_behavior_score: f64,
    pub data_coverage_score: f64,
    pub activity_score: f64,
    pub discovery_score: f64,
    pub rank_tier: String,
    pub promoted: bool,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct TraderBehaviorProfile {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub rolling_consistency_score_7d: f64,
    pub rolling_consistency_score_30d: f64,
    pub avg_daily_pnl_7d: f64,
    pub avg_daily_pnl_30d: f64,
    pub pnl_volatility_7d: f64,
    pub pnl_volatility_30d: f64,
    pub win_rate_30d: f64,
    pub recent_leverage_avg_7d: f64,
    pub leverage_avg_30d: f64,
    pub leverage_peak_30d: f64,
    pub leverage_volatility_30d: f64,
    pub max_drawdown_pct_30d: f64,
    pub avg_hold_duration_secs_30d: f64,
    pub median_hold_duration_secs_30d: f64,
    pub entry_timing_edge_bps_30d: f64,
    pub favorable_entry_rate_30d: f64,
    pub behavioral_drift_score: f64,
    pub emotional_volatility_score: f64,
    pub revenge_trading_score: f64,
    pub sizing_instability_score: f64,
    pub consistency_score_delta: f64,
    pub active_days_30d: i32,
    pub fills_7d: i32,
    pub fills_30d: i32,
    pub lifecycle_stage: String,
    pub window_start: DateTime<Utc>,
    pub window_end: DateTime<Utc>,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct BehavioralAlert {
    pub id: Uuid,
    pub trader_id: Uuid,
    pub alert_type: String,
    pub severity: String,
    pub title: String,
    pub message: String,
    pub metric_value: f64,
    pub threshold_value: f64,
    pub detected_at: DateTime<Utc>,
    pub dedupe_key: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct Signal {
    pub id: Uuid,
    pub coin: String,
    pub direction: String,
    pub score: f64,
    pub confidence_score: f64,
    pub supporting_traders: i32,
    pub quality_weight: f64,
    pub momentum_score: f64,
    pub volatility_score: f64,
    pub regime: String,
    pub market_context: String,
    pub rationale: String,
    pub status: String,
    pub consensus_window_key: String,
    pub signal_window_start: DateTime<Utc>,
    pub signal_window_end: DateTime<Utc>,
    pub activated_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub resolved_at: Option<DateTime<Utc>>,
    pub reference_price: f64,
    pub timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct SignalContributor {
    pub id: Uuid,
    pub signal_id: Uuid,
    pub trader_id: Uuid,
    pub wallet: String,
    pub trader_score: f64,
    pub weight: f64,
    pub entry_price: f64,
    pub entry_timestamp: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct SignalOutcome {
    pub id: Uuid,
    pub signal_id: Uuid,
    pub horizon_minutes: i32,
    pub entry_price: f64,
    pub exit_price: f64,
    pub realized_return_bps: f64,
    pub max_favorable_excursion_bps: f64,
    pub max_adverse_excursion_bps: f64,
    pub outcome_label: String,
    pub resolved_at: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct SimulationBatch {
    pub id: Uuid,
    pub name: String,
    pub dataset_start: DateTime<Utc>,
    pub dataset_end: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct SimulationRun {
    pub id: Uuid,
    pub batch_id: Uuid,
    pub strategy_key: String,
    pub strategy_name: String,
    pub dataset_start: DateTime<Utc>,
    pub dataset_end: DateTime<Utc>,
    pub starting_equity: f64,
    pub ending_equity: f64,
    pub total_return_pct: f64,
    pub max_drawdown_pct: f64,
    pub win_rate_pct: f64,
    pub trade_count: i32,
    pub sharpe_like: f64,
    pub expectancy: f64,
    pub profit_factor: f64,
    pub avg_trade_return_pct: f64,
    pub config_json: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct SimulationReport {
    pub id: Uuid,
    pub run_id: Uuid,
    pub total_fees_paid: f64,
    pub total_slippage_paid: f64,
    pub avg_win_pct: f64,
    pub avg_loss_pct: f64,
    pub payoff_ratio: f64,
    pub return_volatility_pct: f64,
    pub peak_equity: f64,
    pub trough_equity: f64,
    pub max_consecutive_wins: i32,
    pub max_consecutive_losses: i32,
    pub drawdown_curve_json: String,
    pub regime_breakdown_json: String,
    pub created_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize, FromRow)]
pub struct PaperTrade {
    pub id: Uuid,
    pub run_id: Uuid,
    pub signal_id: Option<Uuid>,
    pub strategy_key: String,
    pub coin: String,
    pub direction: String,
    pub entry_price: f64,
    pub exit_price: f64,
    pub size: f64,
    pub fee_paid: f64,
    pub slippage_paid: f64,
    pub pnl: f64,
    pub entry_reason: String,
    pub exit_reason: String,
    pub regime: String,
    pub entry_fee_paid: f64,
    pub exit_fee_paid: f64,
    pub entry_slippage_bps: f64,
    pub exit_slippage_bps: f64,
    pub return_pct: f64,
    pub max_favorable_excursion_pct: f64,
    pub max_adverse_excursion_pct: f64,
    pub equity_before: f64,
    pub equity_after: f64,
    pub risk_fraction: f64,
    pub holding_minutes: i32,
    pub confidence_score: f64,
    pub opened_at: DateTime<Utc>,
    pub closed_at: DateTime<Utc>,
    pub created_at: DateTime<Utc>,
}
