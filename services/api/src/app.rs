use crate::repository::Repository;
use anyhow::Result;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::get,
    Json, Router,
};
use chrono::Utc;
use hyperion_config::Settings;
use hyperion_schemas::{
    DiscoveryRankingQuery, HealthResponse, LeaderboardQuery, SignalQuery, TradeJournalQuery,
};
use hyperion_utils::{wait_for_shutdown, Readiness, ServiceMetrics};
use serde::Deserialize;
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use tower_http::trace::TraceLayer;
use tracing::info;
use uuid::Uuid;

#[derive(Clone)]
struct AppState {
    repository: Arc<Repository>,
    readiness: Readiness,
    metrics: Arc<ServiceMetrics>,
}

pub async fn run(settings: Settings) -> Result<()> {
    let pool = PgPoolOptions::new()
        .max_connections(10)
        .connect(&settings.database_url)
        .await?;

    let readiness = Readiness::new();
    readiness.mark_ready();
    let app = Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/metrics", get(metrics))
        .route("/traders/leaderboard", get(leaderboard))
        .route("/traders/discovery/rankings", get(discovery_rankings))
        .route("/traders/{wallet}", get(trader_detail))
        .route("/scores", get(scores))
        .route("/signals", get(signals))
        .route("/signals/active", get(active_signals))
        .route("/signals/history", get(historical_signals))
        .route("/signals/{signal_id}/outcomes", get(signal_outcomes))
        .route("/simulations/latest", get(latest_simulation))
        .route(
            "/simulations/batches/{batch_id}/compare",
            get(simulation_comparison),
        )
        .route("/simulations/runs/{run_id}/report", get(simulation_report))
        .route(
            "/simulations/runs/{run_id}/journal",
            get(simulation_journal),
        )
        .route("/internal/data/freshness", get(data_freshness_live))
        .route("/internal/data/week1-health", get(week1_health))
        .route(
            "/internal/scanner/coverage/snapshots",
            get(scanner_coverage_snapshots).post(record_scanner_coverage_snapshot),
        )
        .route(
            "/internal/ingest/connection-events",
            get(ingest_connection_events),
        )
        .with_state(AppState {
            repository: Arc::new(Repository::new(pool)),
            readiness,
            metrics: Arc::new(ServiceMetrics::default()),
        })
        .layer(TraceLayer::new_for_http());

    let listener = tokio::net::TcpListener::bind(settings.api_bind_addr).await?;
    info!(addr = %settings.api_bind_addr, "api listening");

    let server = tokio::spawn(async move { axum::serve(listener, app).await });
    wait_for_shutdown().await;
    server.abort();
    Ok(())
}

async fn live() -> Json<HealthResponse> {
    Json(HealthResponse {
        service: "api".to_string(),
        status: "up".to_string(),
        timestamp: Utc::now(),
    })
}

async fn ready(State(state): State<AppState>) -> (StatusCode, Json<HealthResponse>) {
    let status = if state.readiness.is_ready() {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (
        status,
        Json(HealthResponse {
            service: "api".to_string(),
            status: if status == StatusCode::OK {
                "ready".to_string()
            } else {
                "degraded".to_string()
            },
            timestamp: Utc::now(),
        }),
    )
}

async fn metrics(State(state): State<AppState>) -> String {
    state.metrics.render_prometheus("api")
}

async fn leaderboard(
    State(state): State<AppState>,
    Query(query): Query<LeaderboardQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .leaderboard(&query)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn discovery_rankings(
    State(state): State<AppState>,
    Query(query): Query<DiscoveryRankingQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .discovery_rankings(&query)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn signals(
    State(state): State<AppState>,
    Query(query): Query<SignalQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .list_signals(&query, None)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn active_signals(
    State(state): State<AppState>,
    Query(query): Query<SignalQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .list_signals(&query, Some("active"))
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn historical_signals(
    State(state): State<AppState>,
    Query(query): Query<SignalQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .list_signals(&query, Some("__historical__"))
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn scores(State(state): State<AppState>) -> Result<Json<serde_json::Value>, StatusCode> {
    let scores = state
        .repository
        .latest_scores(100)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let ids: Vec<Uuid> = scores.iter().map(|score| score.trader_id).collect();
    let copy_map = state
        .repository
        .latest_copyability_snapshots_for_traders(&ids)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let mut items = Vec::<serde_json::Value>::with_capacity(scores.len());
    for score in scores {
        let mut base = serde_json::to_value(&score).unwrap_or(serde_json::Value::Null);
        if let serde_json::Value::Object(ref mut object) = base {
            let advisory = copy_map.get(&score.trader_id).cloned();
            object.insert(
                "copyability_advisory".to_string(),
                serde_json::to_value(advisory).unwrap_or(serde_json::Value::Null),
            );
        }
        items.push(base);
    }
    Ok(Json(serde_json::json!({ "items": items })))
}

async fn latest_simulation(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .latest_simulation()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(run) => Ok(Json(serde_json::json!(run))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn simulation_comparison(
    State(state): State<AppState>,
    Path(batch_id): Path<uuid::Uuid>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .simulation_comparison(batch_id)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(batch) => Ok(Json(serde_json::json!(batch))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn simulation_report(
    State(state): State<AppState>,
    Path(run_id): Path<uuid::Uuid>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .simulation_report(run_id)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(report) => Ok(Json(serde_json::json!(report))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn simulation_journal(
    State(state): State<AppState>,
    Path(run_id): Path<uuid::Uuid>,
    Query(query): Query<TradeJournalQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .trade_journal(run_id, query.pagination.page, query.pagination.limit)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(journal) => Ok(Json(serde_json::json!(journal))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn trader_detail(
    State(state): State<AppState>,
    Path(wallet): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .trader_behavior_detail(&wallet)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(detail) => Ok(Json(serde_json::json!(detail))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn signal_outcomes(
    State(state): State<AppState>,
    Path(signal_id): Path<uuid::Uuid>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .signal_outcomes(signal_id)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(detail) => Ok(Json(serde_json::json!(detail))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

#[derive(Debug, Deserialize)]
struct InternalLimitQuery {
    limit: Option<i64>,
}

async fn data_freshness_live(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .data_freshness_live()
        .await
        .map(Json)
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn scanner_coverage_snapshots(
    State(state): State<AppState>,
    Query(q): Query<InternalLimitQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let limit = q.limit.unwrap_or(48);
    state
        .repository
        .scanner_coverage_snapshots(limit)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn record_scanner_coverage_snapshot(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .record_scanner_coverage_snapshot()
        .await
        .map(|id| Json(serde_json::json!({ "id": id })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn ingest_connection_events(
    State(state): State<AppState>,
    Query(q): Query<InternalLimitQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let limit = q.limit.unwrap_or(100);
    state
        .repository
        .ingest_connection_events(limit)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}

async fn week1_health(
    State(state): State<AppState>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    let summary = state
        .repository
        .week1_health_summary()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let channels = state
        .repository
        .week1_channel_health()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(serde_json::json!({
        "summary": summary,
        "channels": channels,
    })))
}
