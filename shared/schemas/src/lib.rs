use chrono::{DateTime, Utc};
use hyperion_models::{
    BehavioralAlert, PaperTrade, Signal, SignalContributor, SignalOutcome, SimulationBatch,
    SimulationReport, SimulationRun, Trader, TraderBehaviorProfile, TraderDiscoveryRank,
    TraderScore,
};
use serde::{Deserialize, Serialize};
use sqlx::FromRow;
use uuid::Uuid;

#[derive(Debug, Clone, Deserialize)]
pub struct Pagination {
    #[serde(default = "default_page", deserialize_with = "deserialize_i64")]
    pub page: i64,
    #[serde(default = "default_limit", deserialize_with = "deserialize_i64")]
    pub limit: i64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LeaderboardQuery {
    #[serde(flatten)]
    pub pagination: Pagination,
    #[serde(default, deserialize_with = "deserialize_optional_f64")]
    pub min_score: Option<f64>,
    pub style: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DiscoveryRankingQuery {
    #[serde(flatten)]
    pub pagination: Pagination,
    #[serde(default, deserialize_with = "deserialize_optional_f64")]
    pub min_score: Option<f64>,
    pub tier: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_bool")]
    pub promoted_only: Option<bool>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SignalQuery {
    #[serde(flatten)]
    pub pagination: Pagination,
    pub coin: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_f64")]
    pub min_score: Option<f64>,
    pub status: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct TradeJournalQuery {
    #[serde(flatten)]
    pub pagination: Pagination,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SimulationTriggerRequest {
    pub replay_days: Option<i64>,
    pub strategies: Option<Vec<String>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct HealthResponse {
    pub service: String,
    pub status: String,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct MetricsResponse {
    pub service: String,
    pub reconnects: u64,
    pub messages_received: u64,
    pub parse_failures: u64,
    pub db_write_failures: u64,
}

#[derive(Debug, Clone, Serialize, FromRow)]
pub struct LeaderboardItem {
    pub wallet: String,
    pub total_score: f64,
    pub style: String,
    pub last_seen: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TraderDetailResponse {
    pub trader_id: Uuid,
    pub wallet: String,
    pub latest_score: Option<TraderScore>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TraderDiscoveryRankingListResponse {
    pub items: Vec<TraderDiscoveryRank>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TimeSeriesPoint {
    pub timestamp: DateTime<Utc>,
    pub value: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct RollingPnlAnalytics {
    pub avg_daily_pnl_7d: f64,
    pub avg_daily_pnl_30d: f64,
    pub pnl_volatility_7d: f64,
    pub pnl_volatility_30d: f64,
    pub win_rate_30d: f64,
    pub equity_change_30d: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct HoldDurationAnalysis {
    pub average_seconds: f64,
    pub median_seconds: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct EntryTimingAnalysis {
    pub average_edge_bps: f64,
    pub favorable_entry_rate: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct BehavioralDriftAnalysis {
    pub drift_score: f64,
    pub consistency_delta: f64,
    pub emotional_volatility_score: f64,
    pub revenge_trading_score: f64,
    pub sizing_instability_score: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CopyabilityAdvisorySnapshot {
    pub copyability_score: f64,
    pub signal_frequency_component: f64,
    pub timing_component: f64,
    pub realized_ratio_component: f64,
    pub stability_component: f64,
    pub computed_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TraderLifecycleMetrics {
    pub first_seen: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub age_days: i64,
    pub days_since_last_seen: i64,
    pub active_days_30d: i32,
    pub fills_7d: i32,
    pub fills_30d: i32,
    pub lifecycle_stage: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct TraderBehaviorDetailResponse {
    pub trader: Trader,
    pub latest_score: Option<TraderScore>,
    pub copyability_advisory: Option<CopyabilityAdvisorySnapshot>,
    pub latest_behavior_profile: Option<TraderBehaviorProfile>,
    pub rolling_pnl_analytics: Option<RollingPnlAnalytics>,
    pub leverage_history: Vec<TimeSeriesPoint>,
    pub drawdown_history: Vec<TimeSeriesPoint>,
    pub consistency_history: Vec<TimeSeriesPoint>,
    pub hold_duration_analysis: Option<HoldDurationAnalysis>,
    pub entry_timing_analysis: Option<EntryTimingAnalysis>,
    pub behavioral_drift: Option<BehavioralDriftAnalysis>,
    pub lifecycle_metrics: Option<TraderLifecycleMetrics>,
    pub alerts: Vec<BehavioralAlert>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SignalListResponse {
    pub items: Vec<SignalSummaryResponse>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SignalSummaryResponse {
    pub signal: Signal,
    pub contributors: Vec<SignalContributor>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SignalOutcomesResponse {
    pub signal: Signal,
    pub outcomes: Vec<SignalOutcome>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SimulationRunSummaryResponse {
    pub run: SimulationRun,
    pub report: Option<SimulationReport>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SimulationComparisonResponse {
    pub batch: SimulationBatch,
    pub runs: Vec<SimulationRunSummaryResponse>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SimulationReportResponse {
    pub batch: SimulationBatch,
    pub run: SimulationRun,
    pub report: SimulationReport,
}

#[derive(Debug, Clone, Serialize)]
pub struct TradeJournalResponse {
    pub run: SimulationRun,
    pub trades: Vec<PaperTrade>,
}

const fn default_page() -> i64 {
    1
}

const fn default_limit() -> i64 {
    25
}

fn deserialize_i64<'de, D>(deserializer: D) -> Result<i64, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw = String::deserialize(deserializer)?;
    raw.parse::<i64>().map_err(serde::de::Error::custom)
}

fn deserialize_optional_f64<'de, D>(deserializer: D) -> Result<Option<f64>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw = Option::<String>::deserialize(deserializer)?;
    raw.map(|value| value.parse::<f64>().map_err(serde::de::Error::custom))
        .transpose()
}

fn deserialize_optional_bool<'de, D>(deserializer: D) -> Result<Option<bool>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let raw = Option::<String>::deserialize(deserializer)?;
    raw.map(|value| value.parse::<bool>().map_err(serde::de::Error::custom))
        .transpose()
}
