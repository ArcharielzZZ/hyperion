use crate::{
    contracts::{
        AllMidsData, ClearinghouseStateMessage, PerpAssetCtxMessage, RawEnvelope,
        SubscriptionResponseData, UserFillsEvent, UserFillsMessage, UserFundingEvent,
        UserLiquidationEvent, UserNonUserCancelEvent, WsTrade,
    },
    normalizer::{
        FillRecord, LiquidationRecord, MarketSnapshotRecord, NormalizedBatch, PnlSnapshotRecord,
        PositionRecord, TradeTickRecord,
    },
};
use chrono::{DateTime, Utc};
use std::fmt;

#[derive(Debug, Clone)]
pub enum IngestMessage {
    SubscriptionResponse(SubscriptionResponseData),
    AllMids(AllMidsData),
    ActiveAssetCtx(PerpAssetCtxMessage),
    Trades(Vec<WsTrade>),
    UserFills(UserFillsMessage),
    ClearinghouseState(ClearinghouseStateMessage),
    UserLiquidation(UserLiquidationEvent),
    UserFunding(UserFundingEvent),
    UserFillsEvent(UserFillsEvent),
    UserNonUserCancel(UserNonUserCancelEvent),
    Pong,
    Error(String),
    UnknownChannel(String),
}

#[derive(Debug, Clone)]
pub struct ValidationError {
    pub channel: Option<String>,
    pub reason: String,
}

impl ValidationError {
    fn new(channel: Option<&str>, reason: impl Into<String>) -> Self {
        Self {
            channel: channel.map(ToString::to_string),
            reason: reason.into(),
        }
    }
}

impl fmt::Display for ValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.channel {
            Some(channel) => write!(f, "[{channel}] {}", self.reason),
            None => write!(f, "{}", self.reason),
        }
    }
}

impl std::error::Error for ValidationError {}

pub fn parse_message(text: &str) -> Result<IngestMessage, ValidationError> {
    let envelope: RawEnvelope = serde_json::from_str(text)
        .map_err(|error| ValidationError::new(None, format!("invalid websocket JSON: {error}")))?;

    match envelope.channel.as_str() {
        "subscriptionResponse" => parse_data(envelope.channel.as_str(), envelope.data)
            .map(IngestMessage::SubscriptionResponse),
        "allMids" => parse_all_mids(envelope.data).map(IngestMessage::AllMids),
        "activeAssetCtx" => {
            parse_data(envelope.channel.as_str(), envelope.data).map(IngestMessage::ActiveAssetCtx)
        }
        "trades" => parse_data(envelope.channel.as_str(), envelope.data).map(IngestMessage::Trades),
        "userFills" => {
            parse_data(envelope.channel.as_str(), envelope.data).map(IngestMessage::UserFills)
        }
        "clearinghouseState" => parse_data(envelope.channel.as_str(), envelope.data)
            .map(IngestMessage::ClearinghouseState),
        "user" => parse_user_channel(envelope.data),
        "pong" => Ok(IngestMessage::Pong),
        "error" => {
            let message = serde_json::from_value::<String>(envelope.data).map_err(|error| {
                ValidationError::new(Some("error"), format!("invalid error payload: {error}"))
            })?;
            Ok(IngestMessage::Error(message))
        }
        other => Ok(IngestMessage::UnknownChannel(other.to_string())),
    }
}

pub fn normalize_message(
    message: &IngestMessage,
    received_at: DateTime<Utc>,
) -> Result<NormalizedBatch, ValidationError> {
    let mut batch = NormalizedBatch::default();

    match message {
        IngestMessage::SubscriptionResponse(_)
        | IngestMessage::Pong
        | IngestMessage::Error(_)
        | IngestMessage::UnknownChannel(_)
        | IngestMessage::UserFunding(_)
        | IngestMessage::UserFillsEvent(_)
        | IngestMessage::UserNonUserCancel(_) => {}
        IngestMessage::AllMids(message) => {
            for (coin, price) in &message.mids {
                let price = parse_f64("allMids.price", price, "allMids")?;
                batch.market_snapshots.push(MarketSnapshotRecord {
                    coin: coin.clone(),
                    funding_rate: 0.0,
                    open_interest: 0.0,
                    price,
                    timestamp: received_at,
                    snapshot_key: format!("allMids|{coin}|{price:.12}"),
                });
            }
        }
        IngestMessage::ActiveAssetCtx(message) => {
            let funding = parse_f64(
                "activeAssetCtx.funding",
                &message.ctx.funding,
                "activeAssetCtx",
            )?;
            let open_interest = parse_f64(
                "activeAssetCtx.openInterest",
                &message.ctx.open_interest,
                "activeAssetCtx",
            )?;
            let price = parse_f64(
                "activeAssetCtx.price",
                message
                    .ctx
                    .mid_px
                    .as_deref()
                    .unwrap_or(message.ctx.mark_px.as_str()),
                "activeAssetCtx",
            )?;

            batch.market_snapshots.push(MarketSnapshotRecord {
                coin: message.coin.clone(),
                funding_rate: funding,
                open_interest,
                price,
                timestamp: received_at,
                snapshot_key: format!(
                    "activeAssetCtx|{}|{funding:.12}|{open_interest:.12}|{price:.12}",
                    message.coin
                ),
            });
        }
        IngestMessage::Trades(trades) => {
            for trade in trades {
                let timestamp = timestamp_from_millis(trade.time, "trades")?;
                let price = parse_f64("trades.px", &trade.px, "trades")?;
                let size = parse_f64("trades.sz", &trade.sz, "trades")?;
                batch.trade_ticks.push(TradeTickRecord {
                    coin: trade.coin.clone(),
                    side: trade.side.as_normalized().to_string(),
                    size,
                    price,
                    trade_hash: trade.hash.clone(),
                    trade_id: trade.tid,
                    buyer_wallet: Some(trade.users[0].clone()),
                    seller_wallet: Some(trade.users[1].clone()),
                    timestamp,
                    event_key: format!(
                        "trade|{}|{}|{}|{}",
                        trade.coin, trade.time, trade.hash, trade.tid
                    ),
                });
            }
        }
        IngestMessage::UserFills(message) => {
            for fill in &message.fills {
                let timestamp = timestamp_from_millis(fill.time, "userFills")?;
                let price = parse_f64("userFills.px", &fill.px, "userFills")?;
                let size = parse_f64("userFills.sz", &fill.sz, "userFills")?;

                let closed_pnl = parse_f64("userFills.closedPnl", &fill.closed_pnl, "userFills").ok();
                let fee = parse_f64("userFills.fee", &fill.fee, "userFills").ok();

                batch.fills.push(FillRecord {
                    wallet: message.user.clone(),
                    coin: fill.coin.clone(),
                    side: fill.side.as_normalized().to_string(),
                    size,
                    leverage: 0.0,
                    price,
                    timestamp,
                    event_key: format!(
                        "fill|{}|{}|{}|{}|{}",
                        message.user, fill.coin, fill.hash, fill.tid, fill.oid
                    ),
                    fill_dir: Some(fill.dir.clone()),
                    closed_pnl_usd: closed_pnl,
                    fee_usd: fee,
                });
            }
        }
        IngestMessage::ClearinghouseState(message) => {
            let state = &message.clearinghouse_state;
            let timestamp = timestamp_from_millis(state.time, "clearinghouseState")?;
            let mut unrealized_total = 0.0;

            for asset_position in &state.asset_positions {
                let signed_size = parse_f64(
                    "clearinghouseState.szi",
                    &asset_position.position.szi,
                    "clearinghouseState",
                )?;
                let entry_price = parse_f64(
                    "clearinghouseState.entryPx",
                    &asset_position.position.entry_px,
                    "clearinghouseState",
                )?;
                let unrealized_pnl = parse_f64(
                    "clearinghouseState.unrealizedPnl",
                    &asset_position.position.unrealized_pnl,
                    "clearinghouseState",
                )?;
                unrealized_total += unrealized_pnl;

                batch.positions.push(PositionRecord {
                    wallet: message.user.clone(),
                    coin: asset_position.position.coin.clone(),
                    direction: if signed_size >= 0.0 {
                        "long".to_string()
                    } else {
                        "short".to_string()
                    },
                    entry_price,
                    size: signed_size.abs(),
                    leverage: asset_position.position.leverage.value,
                    unrealized_pnl,
                    timestamp,
                    snapshot_key: format!(
                        "position|{}|{}|{}|{}|{}",
                        message.user,
                        asset_position.position.coin,
                        asset_position.position.szi,
                        asset_position.position.entry_px,
                        asset_position.position.leverage.value
                    ),
                });
            }

            let equity = parse_f64(
                "clearinghouseState.accountValue",
                &state.margin_summary.account_value,
                "clearinghouseState",
            )?;

            batch.pnl_snapshots.push(PnlSnapshotRecord {
                wallet: message.user.clone(),
                equity,
                realized_pnl: 0.0,
                unrealized_pnl: unrealized_total,
                timestamp,
                snapshot_key: format!(
                    "pnl|{}|{}|{}|{}",
                    message.user, state.time, state.margin_summary.account_value, unrealized_total
                ),
            });
        }
        IngestMessage::UserLiquidation(message) => {
            let liquidation = &message.liquidation;
            batch.liquidations.push(LiquidationRecord {
                liquidator_wallet: liquidation.liquidator.clone(),
                liquidated_wallet: liquidation.liquidated_user.clone(),
                liquidated_notional: parse_f64(
                    "user.liquidated_ntl_pos",
                    &liquidation.liquidated_ntl_pos,
                    "user",
                )?,
                liquidated_account_value: parse_f64(
                    "user.liquidated_account_value",
                    &liquidation.liquidated_account_value,
                    "user",
                )?,
                liquidation_id: liquidation.lid,
                timestamp: received_at,
                event_key: format!("liquidation|{}", liquidation.lid),
            });
        }
    }

    Ok(batch)
}

fn parse_all_mids(data: serde_json::Value) -> Result<AllMidsData, ValidationError> {
    if let Ok(parsed) = serde_json::from_value::<AllMidsData>(data.clone()) {
        return Ok(parsed);
    }
    let mids = serde_json::from_value::<std::collections::HashMap<String, String>>(data).map_err(
        |error| {
            ValidationError::new(
                Some("allMids"),
                format!("payload failed schema validation: {error}"),
            )
        },
    )?;
    Ok(AllMidsData { mids })
}

fn parse_user_channel(data: serde_json::Value) -> Result<IngestMessage, ValidationError> {
    if data.get("liquidation").is_some() {
        parse_user_data("user", data).map(IngestMessage::UserLiquidation)
    } else if data.get("funding").is_some() {
        parse_user_data::<UserFundingWrapper>("user", data)
            .map(|wrapper| IngestMessage::UserFunding(wrapper.funding))
    } else if data.get("fills").is_some() {
        parse_user_data("user", data).map(IngestMessage::UserFillsEvent)
    } else if data.get("nonUserCancel").is_some() {
        parse_user_data("user", data).map(IngestMessage::UserNonUserCancel)
    } else {
        Ok(IngestMessage::UnknownChannel("user".to_string()))
    }
}

fn parse_data<T: serde::de::DeserializeOwned>(
    channel: &str,
    data: serde_json::Value,
) -> Result<T, ValidationError> {
    serde_json::from_value(data).map_err(|error| {
        ValidationError::new(
            Some(channel),
            format!("payload failed schema validation: {error}"),
        )
    })
}

fn parse_user_data<T: serde::de::DeserializeOwned>(
    channel: &str,
    data: serde_json::Value,
) -> Result<T, ValidationError> {
    parse_data(channel, data)
}

fn parse_f64(field: &str, raw: &str, channel: &str) -> Result<f64, ValidationError> {
    raw.parse::<f64>().map_err(|error| {
        ValidationError::new(
            Some(channel),
            format!("invalid numeric field {field}={raw:?}: {error}"),
        )
    })
}

fn timestamp_from_millis(millis: i64, channel: &str) -> Result<DateTime<Utc>, ValidationError> {
    DateTime::<Utc>::from_timestamp_millis(millis).ok_or_else(|| {
        ValidationError::new(
            Some(channel),
            format!("invalid millisecond timestamp: {millis}"),
        )
    })
}

#[derive(Debug, Clone, serde::Deserialize)]
struct UserFundingWrapper {
    funding: UserFundingEvent,
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use std::path::PathBuf;

    fn fixture(name: &str) -> String {
        std::fs::read_to_string(fixtures_dir().join(name)).expect("fixture must exist")
    }

    fn fixtures_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
    }

    #[test]
    fn parses_trade_fixture() {
        let raw = fixture("trades_btc.json");
        let message = parse_message(&raw).expect("trade fixture should parse");
        let batch = normalize_message(
            &message,
            Utc.timestamp_millis_opt(1_778_536_578_631).unwrap(),
        )
        .expect("trade fixture should normalize");
        assert_eq!(batch.trade_ticks.len(), 2);
        assert_eq!(batch.trade_ticks[0].coin, "BTC");
        assert_eq!(batch.trade_ticks[0].trade_id, 101);
        assert_eq!(batch.trade_ticks[0].trade_hash, "0xtrade001");
        assert_eq!(
            batch.trade_ticks[0].buyer_wallet.as_deref(),
            Some("0xbuyer")
        );
        assert_eq!(
            batch.trade_ticks[0].seller_wallet.as_deref(),
            Some("0xseller")
        );
    }

    #[test]
    fn parses_active_asset_ctx_fixture() {
        let raw = fixture("active_asset_ctx_btc.json");
        let message = parse_message(&raw).expect("active asset ctx should parse");
        let batch = normalize_message(
            &message,
            Utc.timestamp_millis_opt(1_778_536_580_000).unwrap(),
        )
        .expect("active asset ctx should normalize");
        assert_eq!(batch.market_snapshots.len(), 1);
        assert_eq!(batch.market_snapshots[0].coin, "BTC");
    }

    #[test]
    fn parses_all_mids_fixture() {
        let raw = fixture("all_mids_trimmed.json");
        let message = parse_message(&raw).expect("all mids should parse");
        let batch = normalize_message(&message, Utc::now()).expect("all mids should normalize");
        assert_eq!(batch.market_snapshots.len(), 3);
    }

    #[test]
    fn parses_user_fills_fixture() {
        let raw = fixture("user_fills_snapshot.json");
        let message = parse_message(&raw).expect("user fills should parse");
        let batch = normalize_message(&message, Utc::now()).expect("user fills should normalize");
        assert_eq!(batch.fills.len(), 2);
        assert_eq!(
            batch.fills[0].wallet,
            "0x31ca8395cf837de08b24da3f660e77761dfb974b"
        );
    }

    #[test]
    fn parses_clearinghouse_state_fixture() {
        let raw = fixture("clearinghouse_state_snapshot.json");
        let message = parse_message(&raw).expect("clearinghouse state should parse");
        let batch =
            normalize_message(&message, Utc::now()).expect("clearinghouse state should normalize");
        assert_eq!(batch.positions.len(), 2);
        assert_eq!(batch.pnl_snapshots.len(), 1);
    }

    #[test]
    fn parses_liquidation_fixture() {
        let raw = fixture("user_liquidation_event.json");
        let message = parse_message(&raw).expect("liquidation event should parse");
        let batch = normalize_message(&message, Utc::now()).expect("liquidation should normalize");
        assert_eq!(batch.liquidations.len(), 1);
        assert_eq!(batch.liquidations[0].liquidation_id, 991337);
    }

    #[test]
    fn rejects_malformed_fixture() {
        let raw = fixture("malformed_trade.json");
        let message = parse_message(&raw).expect("malformed trade should parse JSON envelope");
        let error =
            normalize_message(&message, Utc::now()).expect_err("numeric validation should fail");
        assert!(error.reason.contains("invalid numeric field"));
    }
}
