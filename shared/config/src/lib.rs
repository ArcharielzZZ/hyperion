use anyhow::{Context, Result};
use std::{env, net::SocketAddr, time::Duration};

#[derive(Debug, Clone)]
pub struct Settings {
    pub app_env: String,
    pub log_format: String,
    pub log_filter: String,
    pub database_url: String,
    pub redis_url: String,
    pub api_bind_addr: SocketAddr,
    pub ingest_bind_addr: SocketAddr,
    pub trader_engine_bind_addr: SocketAddr,
    pub signal_engine_bind_addr: SocketAddr,
    pub execution_engine_bind_addr: SocketAddr,
    pub hyperliquid: HyperliquidConfig,
    pub storage: StorageConfig,
    pub trader_engine: TraderEngineConfig,
    pub signal_engine: SignalEngineConfig,
    pub execution_engine: ExecutionEngineConfig,
}

#[derive(Debug, Clone)]
pub struct HyperliquidConfig {
    pub ws_url: String,
    pub dex: Option<String>,
    pub heartbeat_interval: Duration,
    pub receive_timeout: Duration,
    pub base_backoff: Duration,
    pub max_backoff: Duration,
    pub tracked_users: Vec<String>,
    pub tracked_coins: Vec<String>,
    pub aggregate_fill_snapshots: bool,
    pub subscribe_all_trade_coins: bool,
    pub max_trade_coins: usize,
    pub auto_discover_traders: bool,
    pub max_discovered_users: usize,
}

#[derive(Debug, Clone)]
pub struct StorageConfig {
    pub trader_last_seen_update_interval: Duration,
    pub maintenance_interval: Duration,
    pub trade_ticks_retention_days: i64,
    pub market_snapshots_retention_days: i64,
}

#[derive(Debug, Clone)]
pub struct TraderEngineConfig {
    pub recalculation_interval: Duration,
}

#[derive(Debug, Clone)]
pub struct SignalEngineConfig {
    pub recalculation_interval: Duration,
    pub min_confirmations: usize,
    pub min_score: f64,
    pub signal_window_secs: i64,
    pub signal_expiry_mins: i64,
}

#[derive(Debug, Clone)]
pub struct ExecutionEngineConfig {
    pub simulation_interval: Duration,
    pub fee_bps: f64,
    pub slippage_bps: f64,
    pub replay_days: i64,
    pub starting_equity: f64,
}

impl Settings {
    pub fn load() -> Result<Self> {
        let _ = dotenvy::dotenv();
        Ok(Self {
            app_env: env_var("APP_ENV", "local"),
            log_format: env_var("LOG_FORMAT", "pretty"),
            log_filter: env_var("LOG_FILTER", "info,hyperion=debug"),
            database_url: env::var("DATABASE_URL").context("DATABASE_URL is required")?,
            redis_url: env_var("REDIS_URL", "redis://localhost:6379"),
            api_bind_addr: socket_addr("API_BIND_ADDR", "127.0.0.1:8080")?,
            ingest_bind_addr: socket_addr("INGEST_BIND_ADDR", "127.0.0.1:8081")?,
            trader_engine_bind_addr: socket_addr("TRADER_ENGINE_BIND_ADDR", "127.0.0.1:8082")?,
            signal_engine_bind_addr: socket_addr("SIGNAL_ENGINE_BIND_ADDR", "127.0.0.1:8083")?,
            execution_engine_bind_addr: socket_addr(
                "EXECUTION_ENGINE_BIND_ADDR",
                "127.0.0.1:8084",
            )?,
            hyperliquid: HyperliquidConfig {
                ws_url: env_var("HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws"),
                dex: env::var("HYPERLIQUID_DEX")
                    .ok()
                    .and_then(|value| (!value.trim().is_empty()).then_some(value)),
                heartbeat_interval: Duration::from_secs(env_u64("HYPERLIQUID_HEARTBEAT_SECS", 15)),
                receive_timeout: Duration::from_secs(env_u64(
                    "HYPERLIQUID_RECEIVE_TIMEOUT_SECS",
                    45,
                )),
                base_backoff: Duration::from_secs(env_u64("HYPERLIQUID_BASE_BACKOFF_SECS", 1)),
                max_backoff: Duration::from_secs(env_u64("HYPERLIQUID_MAX_BACKOFF_SECS", 60)),
                tracked_users: env_csv("HYPERLIQUID_TRACKED_USERS"),
                tracked_coins: env_csv("HYPERLIQUID_TRACKED_COINS"),
                aggregate_fill_snapshots: env_bool("HYPERLIQUID_AGGREGATE_FILL_SNAPSHOTS", false),
                subscribe_all_trade_coins: env_bool("HYPERLIQUID_SUBSCRIBE_ALL_TRADE_COINS", true),
                max_trade_coins: env_usize("HYPERLIQUID_MAX_TRADE_COINS", 1000),
                auto_discover_traders: env_bool("HYPERLIQUID_AUTO_DISCOVER_TRADERS", true),
                max_discovered_users: env_usize("HYPERLIQUID_MAX_DISCOVERED_USERS", 500),
            },
            storage: StorageConfig {
                trader_last_seen_update_interval: Duration::from_secs(env_u64(
                    "STORAGE_TRADER_LAST_SEEN_UPDATE_SECS",
                    600,
                )),
                maintenance_interval: Duration::from_secs(env_u64(
                    "STORAGE_MAINTENANCE_INTERVAL_SECS",
                    3600,
                )),
                trade_ticks_retention_days: env_i64("STORAGE_TRADE_TICKS_RETENTION_DAYS", 21)
                    .max(1),
                market_snapshots_retention_days: env_i64(
                    "STORAGE_MARKET_SNAPSHOTS_RETENTION_DAYS",
                    45,
                )
                .max(1),
            },
            trader_engine: TraderEngineConfig {
                recalculation_interval: Duration::from_secs(env_u64(
                    "TRADER_ENGINE_RECALC_INTERVAL_SECS",
                    60,
                )),
            },
            signal_engine: SignalEngineConfig {
                recalculation_interval: Duration::from_secs(env_u64(
                    "SIGNAL_ENGINE_RECALC_INTERVAL_SECS",
                    30,
                )),
                min_confirmations: env_usize("SIGNAL_ENGINE_MIN_CONFIRMATIONS", 3),
                min_score: env_f64("SIGNAL_ENGINE_MIN_SCORE", 65.0),
                signal_window_secs: env_i64("SIGNAL_ENGINE_WINDOW_SECS", 300),
                signal_expiry_mins: env_i64("SIGNAL_ENGINE_EXPIRY_MINS", 240),
            },
            execution_engine: ExecutionEngineConfig {
                simulation_interval: Duration::from_secs(env_u64(
                    "EXECUTION_ENGINE_SIM_INTERVAL_SECS",
                    120,
                )),
                fee_bps: env_f64("EXECUTION_ENGINE_FEE_BPS", 4.0),
                slippage_bps: env_f64("EXECUTION_ENGINE_SLIPPAGE_BPS", 6.0),
                replay_days: env_i64("EXECUTION_ENGINE_REPLAY_DAYS", 7),
                starting_equity: env_f64("EXECUTION_ENGINE_STARTING_EQUITY", 10_000.0),
            },
        })
    }
}

fn env_var(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_u64(key: &str, default: u64) -> u64 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default)
}

fn env_usize(key: &str, default: usize) -> usize {
    env::var(key)
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(default)
}

fn env_i64(key: &str, default: i64) -> i64 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse::<i64>().ok())
        .unwrap_or(default)
}

fn env_f64(key: &str, default: f64) -> f64 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse::<f64>().ok())
        .unwrap_or(default)
}

fn env_bool(key: &str, default: bool) -> bool {
    env::var(key)
        .ok()
        .and_then(|value| match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Some(true),
            "0" | "false" | "no" | "off" => Some(false),
            _ => None,
        })
        .unwrap_or(default)
}

fn env_csv(key: &str) -> Vec<String> {
    env::var(key)
        .unwrap_or_default()
        .split(',')
        .filter_map(|value| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        })
        .collect()
}

fn socket_addr(key: &str, default: &str) -> Result<SocketAddr> {
    env_var(key, default)
        .parse::<SocketAddr>()
        .with_context(|| format!("{key} must be a valid socket address"))
}
