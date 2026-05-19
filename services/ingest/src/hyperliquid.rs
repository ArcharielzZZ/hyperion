use crate::{
    contracts::{Subscription, WsRequest},
    dedup::DedupCache,
    repository::Repository,
    validation::{normalize_message, parse_message, IngestMessage},
};
use anyhow::Result;
use futures_util::{SinkExt, StreamExt};
use hyperion_config::HyperliquidConfig;
use hyperion_utils::ServiceMetrics;
use serde_json::json;
use std::{cmp::min, collections::HashSet, sync::Arc, time::Duration};
use tokio::sync::watch;
use tokio_tungstenite::{connect_async, tungstenite::Message};
use tracing::{debug, info, warn};

#[derive(Debug, Clone)]
struct SubscriptionState {
    subscribed_trade_coins: HashSet<String>,
    subscribed_users: HashSet<String>,
    discovered_users: HashSet<String>,
}

impl SubscriptionState {
    fn from_config(config: &HyperliquidConfig) -> Self {
        Self {
            subscribed_trade_coins: config.tracked_coins.iter().cloned().collect(),
            subscribed_users: config.tracked_users.iter().cloned().collect(),
            discovered_users: HashSet::new(),
        }
    }

    fn initial_subscriptions(&self, config: &HyperliquidConfig) -> Vec<WsRequest> {
        let mut subscriptions = Vec::new();
        subscriptions.push(WsRequest::subscribe(Subscription::AllMids {
            dex: config.dex.clone(),
        }));

        for coin in &config.tracked_coins {
            subscriptions.push(WsRequest::subscribe(Subscription::Trades {
                coin: coin.clone(),
            }));
            subscriptions.push(WsRequest::subscribe(Subscription::ActiveAssetCtx {
                coin: coin.clone(),
            }));
        }

        for user in &config.tracked_users {
            subscriptions.extend(user_subscriptions(config, user));
        }

        for coin in &self.subscribed_trade_coins {
            if config.tracked_coins.iter().any(|tracked| tracked == coin) {
                continue;
            }
            subscriptions.push(WsRequest::subscribe(Subscription::Trades {
                coin: coin.clone(),
            }));
        }

        for user in &self.discovered_users {
            subscriptions.extend(user_subscriptions(config, user));
        }

        subscriptions
    }

    fn discovery_subscriptions(
        &mut self,
        config: &HyperliquidConfig,
        message: &IngestMessage,
    ) -> Vec<WsRequest> {
        let mut subscriptions = Vec::new();

        if config.subscribe_all_trade_coins {
            if let IngestMessage::AllMids(all_mids) = message {
                let remaining = config
                    .max_trade_coins
                    .saturating_sub(self.subscribed_trade_coins.len());
                if remaining > 0 {
                    let mut candidates = all_mids
                        .mids
                        .keys()
                        .filter(|coin| is_public_trade_coin(coin))
                        .cloned()
                        .collect::<Vec<_>>();
                    candidates.sort();

                    for coin in candidates.into_iter().take(remaining) {
                        if self.subscribed_trade_coins.insert(coin.clone()) {
                            subscriptions.push(WsRequest::subscribe(Subscription::Trades { coin }));
                        }
                    }
                }
            }
        }

        if config.auto_discover_traders {
            if let IngestMessage::Trades(trades) = message {
                let remaining = config
                    .max_discovered_users
                    .saturating_sub(self.discovered_users.len());
                if remaining > 0 {
                    let mut candidates = trades
                        .iter()
                        .flat_map(|trade| trade.users.iter())
                        .filter(|wallet| !wallet.trim().is_empty())
                        .cloned()
                        .collect::<Vec<_>>();
                    candidates.sort();
                    candidates.dedup();

                    for user in candidates.into_iter().take(remaining) {
                        if self.subscribed_users.contains(&user) {
                            continue;
                        }
                        self.subscribed_users.insert(user.clone());
                        self.discovered_users.insert(user.clone());
                        subscriptions.extend(user_subscriptions(config, &user));
                    }
                }
            }
        }

        subscriptions
    }
}

pub async fn run_loop(
    config: HyperliquidConfig,
    repository: Arc<Repository>,
    metrics: Arc<ServiceMetrics>,
    mut shutdown_rx: watch::Receiver<bool>,
) -> Result<()> {
    let mut subscription_state = SubscriptionState::from_config(&config);
    let mut backoff = config.base_backoff;
    let mut dedup = DedupCache::default();

    loop {
        if *shutdown_rx.borrow() {
            info!("stopping websocket client");
            repository
                .append_connection_event("ingest_shutdown_requested", json!({}))
                .await;
            break;
        }

        info!(url = %config.ws_url, "connecting to Hyperliquid websocket");

        match connect_async(&config.ws_url).await {
            Ok((mut stream, _response)) => {
                info!("websocket connection established");
                backoff = config.base_backoff;
                repository
                    .append_connection_event("ws_connected", json!({ "url": config.ws_url }))
                    .await;

                let subscriptions = subscription_state.initial_subscriptions(&config);

                if let Err(error) = send_subscriptions(&mut stream, &subscriptions).await {
                    warn!(?error, "failed to send websocket subscriptions");
                    metrics
                        .reconnects
                        .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    repository
                        .append_connection_event(
                            "ws_subscribe_initial_failed",
                            json!({
                                "error": format!("{error:?}"),
                                "subscription_count": subscriptions.len(),
                            }),
                        )
                        .await;
                    wait_backoff(backoff, &mut shutdown_rx).await;
                    backoff = next_backoff(backoff, config.max_backoff);
                    continue;
                }

                let mut heartbeat = tokio::time::interval(config.heartbeat_interval);
                #[allow(unused_assignments)]
                let mut session_end: &'static str = "inner_loop_exit_unknown";
                loop {
                    tokio::select! {
                        changed = shutdown_rx.changed() => {
                            if changed.is_ok() && *shutdown_rx.borrow() {
                                info!("shutdown signal received");
                                repository
                                    .append_connection_event("ws_shutdown_requested", json!({}))
                                    .await;
                                let _ = stream.close(None).await;
                                return Ok(());
                            }
                        }
                        _ = heartbeat.tick() => {
                            if let Err(error) = stream.send(Message::Ping(Vec::new().into())).await {
                                warn!(?error, "failed to send websocket ping");
                                session_end = "ping_send_failed";
                                repository
                                    .append_connection_event(
                                        "ws_ping_failed",
                                        json!({ "error": format!("{error:?}") }),
                                    )
                                    .await;
                                break;
                            }
                        }
                        message = tokio::time::timeout(config.receive_timeout, stream.next()) => {
                            match message {
                                Ok(Some(Ok(Message::Text(text)))) => {
                                    metrics.messages_received.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                    match parse_message(&text) {
                                        Ok(message) => {
                                            let new_subscriptions = subscription_state.discovery_subscriptions(&config, &message);
                                            if !new_subscriptions.is_empty() {
                                                info!(
                                                    new_subscriptions = new_subscriptions.len(),
                                                    total_trade_coins = subscription_state.subscribed_trade_coins.len(),
                                                    total_users = subscription_state.subscribed_users.len(),
                                                    "expanding Hyperliquid subscriptions"
                                                );
                                                if let Err(error) = send_subscriptions(&mut stream, &new_subscriptions).await {
                                                    warn!(?error, "failed to send discovered subscriptions");
                                                    session_end = "discovery_subscribe_failed";
                                                    repository
                                                        .append_connection_event(
                                                            "ws_subscribe_discovery_failed",
                                                            json!({
                                                                "error": format!("{error:?}"),
                                                                "subscription_count": new_subscriptions.len(),
                                                            }),
                                                        )
                                                        .await;
                                                    break;
                                                }
                                            }

                                            if let Err(error) = handle_message(&repository, &metrics, &mut dedup, &message).await {
                                                metrics.parse_failures.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                                metrics.malformed_messages.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                                warn!(?error, raw = %truncate(text.as_ref()), "failed to process websocket message");
                                            }
                                        }
                                        Err(error) => {
                                            metrics.parse_failures.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                            metrics.malformed_messages.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                            warn!(?error, raw = %truncate(text.as_ref()), "failed to parse websocket message");
                                        }
                                    }
                                }
                                Ok(Some(Ok(Message::Binary(_)))) => {
                                    debug!("ignoring binary websocket frame");
                                }
                                Ok(Some(Ok(Message::Pong(_)))) => {
                                    debug!("received websocket pong");
                                }
                                Ok(Some(Ok(Message::Ping(payload)))) => {
                                    if let Err(error) = stream.send(Message::Pong(payload)).await {
                                        warn!(?error, "failed to answer ping frame");
                                        session_end = "pong_reply_failed";
                                        repository
                                            .append_connection_event(
                                                "ws_pong_reply_failed",
                                                json!({ "error": format!("{error:?}") }),
                                            )
                                            .await;
                                        break;
                                    }
                                }
                                Ok(Some(Ok(Message::Frame(_)))) => {
                                    debug!("ignoring raw frame message");
                                }
                                Ok(Some(Ok(Message::Close(frame)))) => {
                                    warn!(?frame, "websocket closed by remote peer");
                                    session_end = "remote_close";
                                    repository
                                        .append_connection_event(
                                            "ws_remote_close",
                                            json!({ "frame": format!("{frame:?}") }),
                                        )
                                        .await;
                                    break;
                                }
                                Ok(Some(Err(error))) => {
                                    warn!(?error, "websocket stream error");
                                    session_end = "stream_error";
                                    repository
                                        .append_connection_event(
                                            "ws_stream_error",
                                            json!({ "error": format!("{error:?}") }),
                                        )
                                        .await;
                                    break;
                                }
                                Ok(None) => {
                                    warn!("websocket stream ended");
                                    session_end = "stream_ended";
                                    repository
                                        .append_connection_event("ws_stream_ended", json!({}))
                                        .await;
                                    break;
                                }
                                Err(_) => {
                                    warn!(
                                        idle_timeout_secs = config.receive_timeout.as_secs(),
                                        "websocket receive timeout exceeded"
                                    );
                                    session_end = "receive_timeout";
                                    repository
                                        .append_connection_event(
                                            "ws_receive_timeout",
                                            json!({
                                                "timeout_secs": config.receive_timeout.as_secs(),
                                            }),
                                        )
                                        .await;
                                    break;
                                }
                            }
                        }
                    }
                }
                repository
                    .append_connection_event(
                        "ws_session_cycle_complete",
                        json!({
                            "outcome": session_end,
                            "backoff_ms_before_outer_wait": backoff.as_millis(),
                        }),
                    )
                    .await;
            }
            Err(error) => {
                warn!(?error, "websocket connection attempt failed");
                repository
                    .append_connection_event(
                        "ws_connect_failed",
                        json!({
                            "url": config.ws_url,
                            "error": format!("{error:?}"),
                        }),
                    )
                    .await;
            }
        }

        metrics
            .reconnects
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        wait_backoff(backoff, &mut shutdown_rx).await;
        backoff = next_backoff(backoff, config.max_backoff);
    }

    Ok(())
}

async fn handle_message(
    repository: &Repository,
    metrics: &ServiceMetrics,
    dedup: &mut DedupCache,
    message: &IngestMessage,
) -> anyhow::Result<()> {
    match message {
        IngestMessage::SubscriptionResponse(response) => {
            info!(method = %response.method, subscription = %response.subscription, "subscription acknowledged");
            return Ok(());
        }
        IngestMessage::Pong => {
            debug!("received application-level pong");
            return Ok(());
        }
        IngestMessage::Error(message) => {
            warn!(error = %message, "Hyperliquid websocket reported error");
            return Ok(());
        }
        IngestMessage::UnknownChannel(channel) => {
            debug!(%channel, "ignoring unsupported websocket channel");
            return Ok(());
        }
        IngestMessage::UserFunding(_)
        | IngestMessage::UserFillsEvent(_)
        | IngestMessage::UserNonUserCancel(_) => {
            debug!("ignoring non-persistent user event");
            return Ok(());
        }
        _ => {}
    }

    let batch = normalize_message(message, chrono::Utc::now())?;
    if batch.is_empty() {
        debug!("received typed websocket message with no persistent records");
        return Ok(());
    }

    let (batch, dedup_stats) = dedup.filter_batch(batch);
    metrics
        .duplicate_messages
        .fetch_add(dedup_stats.duplicates, std::sync::atomic::Ordering::Relaxed);

    if batch.is_empty() {
        debug!("skipping fully duplicated websocket payload");
        return Ok(());
    }

    match repository.persist_batch(&batch).await {
        Ok(stats) => {
            metrics
                .records_ingested
                .fetch_add(stats.inserted_records, std::sync::atomic::Ordering::Relaxed);
            metrics.duplicate_messages.fetch_add(
                stats.duplicate_records(),
                std::sync::atomic::Ordering::Relaxed,
            );
        }
        Err(error) => {
            metrics
                .db_write_failures
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            return Err(error);
        }
    }

    Ok(())
}

fn user_subscriptions(config: &HyperliquidConfig, user: &str) -> Vec<WsRequest> {
    vec![
        WsRequest::subscribe(Subscription::UserFills {
            user: user.to_string(),
            aggregate_by_time: Some(config.aggregate_fill_snapshots),
        }),
        WsRequest::subscribe(Subscription::ClearinghouseState {
            user: user.to_string(),
            dex: config.dex.clone(),
        }),
        WsRequest::subscribe(Subscription::UserEvents {
            user: user.to_string(),
        }),
    ]
}

fn is_public_trade_coin(coin: &str) -> bool {
    !coin.starts_with('@') && !coin.starts_with('#') && !coin.trim().is_empty()
}

async fn send_subscriptions<S>(
    stream: &mut tokio_tungstenite::WebSocketStream<S>,
    subscriptions: &[WsRequest],
) -> Result<(), tokio_tungstenite::tungstenite::Error>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    for subscription in subscriptions {
        let payload = serde_json::to_string(subscription)
            .expect("typed websocket subscriptions must serialize");
        stream.send(Message::Text(payload.into())).await?;
    }
    Ok(())
}

async fn wait_backoff(backoff: Duration, shutdown_rx: &mut watch::Receiver<bool>) {
    tokio::select! {
        _ = tokio::time::sleep(backoff) => {}
        changed = shutdown_rx.changed() => {
            if changed.is_ok() && *shutdown_rx.borrow() {
                info!("shutdown received while backing off");
            }
        }
    }
}

fn next_backoff(current: Duration, max_backoff: Duration) -> Duration {
    Duration::from_secs(min(
        current.as_secs().saturating_mul(2).max(1),
        max_backoff.as_secs().max(1),
    ))
}

fn truncate(raw: &str) -> &str {
    const MAX_LEN: usize = 512;
    if raw.len() <= MAX_LEN {
        raw
    } else {
        &raw[..MAX_LEN]
    }
}
