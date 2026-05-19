use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WsRequest {
    pub method: String,
    pub subscription: Subscription,
}

impl WsRequest {
    pub fn subscribe(subscription: Subscription) -> Self {
        Self {
            method: "subscribe".to_string(),
            subscription,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "camelCase")]
pub enum Subscription {
    AllMids {
        #[serde(skip_serializing_if = "Option::is_none")]
        dex: Option<String>,
    },
    Trades {
        coin: String,
    },
    ActiveAssetCtx {
        coin: String,
    },
    UserFills {
        user: String,
        #[serde(rename = "aggregateByTime", skip_serializing_if = "Option::is_none")]
        aggregate_by_time: Option<bool>,
    },
    ClearinghouseState {
        user: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        dex: Option<String>,
    },
    UserEvents {
        user: String,
    },
}

#[derive(Debug, Clone, Deserialize)]
pub struct RawEnvelope {
    pub channel: String,
    #[serde(default)]
    pub data: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct SubscriptionResponseData {
    pub method: String,
    pub subscription: serde_json::Value,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AllMidsData {
    pub mids: HashMap<String, String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PerpAssetCtxMessage {
    pub coin: String,
    pub ctx: PerpAssetCtx,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PerpAssetCtx {
    pub funding: String,
    #[serde(rename = "openInterest")]
    pub open_interest: String,
    #[serde(rename = "prevDayPx")]
    pub prev_day_px: String,
    #[serde(rename = "dayNtlVlm")]
    pub day_ntl_vlm: String,
    pub premium: String,
    #[serde(rename = "oraclePx")]
    pub oracle_px: String,
    #[serde(rename = "markPx")]
    pub mark_px: String,
    #[serde(rename = "midPx")]
    pub mid_px: Option<String>,
    #[serde(rename = "impactPxs")]
    pub impact_pxs: Option<[String; 2]>,
    #[serde(rename = "dayBaseVlm")]
    pub day_base_vlm: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WsTrade {
    pub coin: String,
    pub side: RawSide,
    pub px: String,
    pub sz: String,
    pub hash: String,
    pub time: i64,
    pub tid: i64,
    pub users: [String; 2],
}

#[derive(Debug, Clone, Copy, Deserialize)]
pub enum RawSide {
    #[serde(rename = "A")]
    Ask,
    #[serde(rename = "B")]
    Bid,
}

impl RawSide {
    pub fn as_normalized(self) -> &'static str {
        match self {
            Self::Ask => "sell",
            Self::Bid => "buy",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct UserFillsMessage {
    #[serde(default, rename = "isSnapshot")]
    pub is_snapshot: bool,
    pub user: String,
    pub fills: Vec<WsFill>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WsFill {
    pub coin: String,
    pub px: String,
    pub sz: String,
    pub side: RawSide,
    pub time: i64,
    #[serde(rename = "startPosition")]
    pub start_position: String,
    pub dir: String,
    #[serde(rename = "closedPnl")]
    pub closed_pnl: String,
    pub hash: String,
    pub oid: i64,
    pub crossed: bool,
    pub fee: String,
    pub tid: i64,
    #[serde(rename = "feeToken")]
    pub fee_token: String,
    #[serde(default, rename = "builderFee")]
    pub builder_fee: Option<String>,
    #[serde(default)]
    pub liquidation: Option<FillLiquidation>,
    #[serde(default, rename = "twapId")]
    pub twap_id: Option<i64>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FillLiquidation {
    #[serde(default, rename = "liquidatedUser")]
    pub liquidated_user: Option<String>,
    #[serde(rename = "markPx")]
    pub mark_px: f64,
    pub method: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClearinghouseStateMessage {
    pub dex: String,
    pub user: String,
    #[serde(rename = "clearinghouseState")]
    pub clearinghouse_state: ClearinghouseState,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClearinghouseState {
    #[serde(rename = "assetPositions")]
    pub asset_positions: Vec<AssetPosition>,
    #[serde(rename = "marginSummary")]
    pub margin_summary: MarginSummary,
    #[serde(rename = "crossMarginSummary")]
    pub cross_margin_summary: MarginSummary,
    #[serde(rename = "crossMaintenanceMarginUsed")]
    pub cross_maintenance_margin_used: String,
    pub withdrawable: String,
    pub time: i64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct MarginSummary {
    #[serde(rename = "accountValue")]
    pub account_value: String,
    #[serde(rename = "totalNtlPos")]
    pub total_ntl_pos: String,
    #[serde(rename = "totalRawUsd")]
    pub total_raw_usd: String,
    #[serde(rename = "totalMarginUsed")]
    pub total_margin_used: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct AssetPosition {
    #[serde(rename = "type")]
    pub position_type: String,
    pub position: Position,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Position {
    pub coin: String,
    pub szi: String,
    pub leverage: Leverage,
    #[serde(rename = "entryPx")]
    pub entry_px: String,
    #[serde(rename = "positionValue")]
    pub position_value: String,
    #[serde(rename = "unrealizedPnl")]
    pub unrealized_pnl: String,
    #[serde(rename = "returnOnEquity")]
    pub return_on_equity: String,
    #[serde(rename = "liquidationPx")]
    pub liquidation_px: Option<String>,
    #[serde(rename = "marginUsed")]
    pub margin_used: String,
    #[serde(rename = "maxLeverage")]
    pub max_leverage: f64,
    #[serde(rename = "cumFunding")]
    pub cum_funding: CumFunding,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Leverage {
    #[serde(rename = "type")]
    pub leverage_type: String,
    pub value: f64,
    #[serde(default, rename = "rawUsd")]
    pub raw_usd: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CumFunding {
    #[serde(rename = "allTime")]
    pub all_time: String,
    #[serde(rename = "sinceOpen")]
    pub since_open: String,
    #[serde(rename = "sinceChange")]
    pub since_change: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UserFundingEvent {
    pub time: i64,
    pub coin: String,
    pub usdc: String,
    pub szi: String,
    #[serde(rename = "fundingRate")]
    pub funding_rate: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UserLiquidationEvent {
    pub liquidation: WsLiquidation,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WsLiquidation {
    pub lid: i64,
    pub liquidator: String,
    #[serde(rename = "liquidated_user")]
    pub liquidated_user: String,
    #[serde(rename = "liquidated_ntl_pos")]
    pub liquidated_ntl_pos: String,
    #[serde(rename = "liquidated_account_value")]
    pub liquidated_account_value: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UserFillsEvent {
    pub fills: Vec<WsFill>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct UserNonUserCancelEvent {
    #[serde(rename = "nonUserCancel")]
    pub non_user_cancel: Vec<WsNonUserCancel>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct WsNonUserCancel {
    pub coin: String,
    pub oid: i64,
}
