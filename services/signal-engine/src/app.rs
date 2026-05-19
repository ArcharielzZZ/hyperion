use crate::{engine, repository::Repository};
use anyhow::Result;
use axum::{
    extract::{Query, State},
    http::StatusCode,
    routing::get,
    Json, Router,
};
use chrono::Utc;
use hyperion_config::Settings;
use hyperion_schemas::{HealthResponse, SignalQuery};
use hyperion_utils::{wait_for_shutdown, Readiness, ServiceMetrics};
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use tokio::sync::watch;
use tower_http::trace::TraceLayer;
use tracing::{error, info};

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

    let repository = Arc::new(Repository::new(pool));
    let readiness = Readiness::new();
    readiness.mark_ready();
    let service_metrics = Arc::new(ServiceMetrics::default());
    let (shutdown_tx, mut shutdown_rx) = watch::channel(false);

    let scheduler_repository = repository.clone();
    let engine_config = settings.signal_engine.clone();
    let interval = settings.signal_engine.recalculation_interval;
    let scheduler = tokio::spawn(async move {
        let mut ticker = tokio::time::interval(interval);
        loop {
            tokio::select! {
                _ = ticker.tick() => {
                    if let Err(error) = engine::recalculate(scheduler_repository.clone(), &engine_config).await {
                        error!(?error, "signal recalculation failed");
                    }
                }
                changed = shutdown_rx.changed() => {
                    if changed.is_ok() && *shutdown_rx.borrow() {
                        break;
                    }
                }
            }
        }
    });

    let app = Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/metrics", get(metrics_endpoint))
        .route("/signals", get(list_signals))
        .with_state(AppState {
            repository,
            readiness,
            metrics: service_metrics,
        })
        .layer(TraceLayer::new_for_http());

    let listener = tokio::net::TcpListener::bind(settings.signal_engine_bind_addr).await?;
    info!(addr = %settings.signal_engine_bind_addr, "signal-engine listening");

    let server = tokio::spawn(async move { axum::serve(listener, app).await });

    wait_for_shutdown().await;
    let _ = shutdown_tx.send(true);
    scheduler.await?;
    server.abort();
    Ok(())
}

async fn live() -> Json<HealthResponse> {
    Json(HealthResponse {
        service: "signal-engine".to_string(),
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
            service: "signal-engine".to_string(),
            status: if status == StatusCode::OK {
                "ready".to_string()
            } else {
                "degraded".to_string()
            },
            timestamp: Utc::now(),
        }),
    )
}

async fn metrics_endpoint(State(state): State<AppState>) -> String {
    state.metrics.render_prometheus("signal-engine")
}

async fn list_signals(
    State(state): State<AppState>,
    Query(query): Query<SignalQuery>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    state
        .repository
        .list_signals(&query)
        .await
        .map(|items| Json(serde_json::json!({ "items": items })))
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)
}
