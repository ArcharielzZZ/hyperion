use chrono::{DateTime, Utc};

#[derive(Debug, Clone)]
pub struct FillRecord {
    pub wallet: String,
    pub coin: String,
    pub side: String,
    pub size: f64,
    pub leverage: f64,
    pub price: f64,
    pub timestamp: DateTime<Utc>,
    pub event_key: String,
    /// Hyperliquid ``dir`` (e.g., Open Long, Close Short). Omit if parsing fails.
    pub fill_dir: Option<String>,
    /// Exchange-closed realization (HL ``closedPnl``), USD.
    pub closed_pnl_usd: Option<f64>,
    /// Exchange fee magnitude in USD-ish numeric from ``fee``.
    pub fee_usd: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct PositionRecord {
    pub wallet: String,
    pub coin: String,
    pub direction: String,
    pub entry_price: f64,
    pub size: f64,
    pub leverage: f64,
    pub unrealized_pnl: f64,
    pub timestamp: DateTime<Utc>,
    pub snapshot_key: String,
}

#[derive(Debug, Clone)]
pub struct PnlSnapshotRecord {
    pub wallet: String,
    pub equity: f64,
    pub realized_pnl: f64,
    pub unrealized_pnl: f64,
    pub timestamp: DateTime<Utc>,
    pub snapshot_key: String,
}

#[derive(Debug, Clone)]
pub struct MarketSnapshotRecord {
    pub coin: String,
    pub funding_rate: f64,
    pub open_interest: f64,
    pub price: f64,
    pub timestamp: DateTime<Utc>,
    pub snapshot_key: String,
}

#[derive(Debug, Clone)]
pub struct TradeTickRecord {
    pub coin: String,
    pub side: String,
    pub size: f64,
    pub price: f64,
    pub trade_hash: String,
    pub trade_id: i64,
    pub buyer_wallet: Option<String>,
    pub seller_wallet: Option<String>,
    pub timestamp: DateTime<Utc>,
    pub event_key: String,
}

#[derive(Debug, Clone)]
pub struct LiquidationRecord {
    pub liquidator_wallet: String,
    pub liquidated_wallet: String,
    pub liquidated_notional: f64,
    pub liquidated_account_value: f64,
    pub liquidation_id: i64,
    pub timestamp: DateTime<Utc>,
    pub event_key: String,
}

#[derive(Debug, Clone, Default)]
pub struct NormalizedBatch {
    pub fills: Vec<FillRecord>,
    pub positions: Vec<PositionRecord>,
    pub pnl_snapshots: Vec<PnlSnapshotRecord>,
    pub market_snapshots: Vec<MarketSnapshotRecord>,
    pub trade_ticks: Vec<TradeTickRecord>,
    pub liquidations: Vec<LiquidationRecord>,
}

impl NormalizedBatch {
    pub fn is_empty(&self) -> bool {
        self.total_records() == 0
    }

    pub fn total_records(&self) -> u64 {
        (self.fills.len()
            + self.positions.len()
            + self.pnl_snapshots.len()
            + self.market_snapshots.len()
            + self.trade_ticks.len()
            + self.liquidations.len()) as u64
    }
}
