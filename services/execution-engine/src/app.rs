use crate::{engine, repository::Repository};
use anyhow::Result;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use chrono::Utc;
use hyperion_config::{ExecutionEngineConfig, Settings};
use hyperion_schemas::{HealthResponse, SimulationTriggerRequest, TradeJournalQuery};
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
    execution_config: ExecutionEngineConfig,
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

    let simulation_repository = repository.clone();
    let execution_config = settings.execution_engine.clone();
    let interval = settings.execution_engine.simulation_interval;
    let scheduler = tokio::spawn(async move {
        let mut ticker = tokio::time::interval(interval);
        loop {
            tokio::select! {
                _ = ticker.tick() => {
                    if let Err(error) = engine::run_simulation(simulation_repository.clone(), &execution_config).await {
                        error!(?error, "paper simulation failed");
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
        .route("/simulations/latest", get(latest_run))
        .route("/simulations/run", post(trigger_run))
        .route(
            "/simulations/batches/{batch_id}/compare",
            get(batch_comparison),
        )
        .route("/simulations/runs/{run_id}/report", get(run_report))
        .route("/simulations/runs/{run_id}/journal", get(run_journal))
        .with_state(AppState {
            repository,
            readiness,
            metrics: service_metrics,
            execution_config: settings.execution_engine.clone(),
        })
        .layer(TraceLayer::new_for_http());

    let listener = tokio::net::TcpListener::bind(settings.execution_engine_bind_addr).await?;
    info!(addr = %settings.execution_engine_bind_addr, "execution-engine listening");

    let server = tokio::spawn(async move { axum::serve(listener, app).await });
    wait_for_shutdown().await;
    let _ = shutdown_tx.send(true);
    scheduler.await?;
    server.abort();
    Ok(())
}

async fn live() -> Json<HealthResponse> {
    Json(HealthResponse {
        service: "execution-engine".to_string(),
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
            service: "execution-engine".to_string(),
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
    state.metrics.render_prometheus("execution-engine")
}

async fn latest_run(State(state): State<AppState>) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .latest_comparison()
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(run) => Ok(Json(serde_json::json!(run))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn trigger_run(
    State(state): State<AppState>,
    Json(request): Json<SimulationTriggerRequest>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match engine::run_simulation_with_request(
        state.repository.clone(),
        &state.execution_config,
        &request,
    )
    .await
    .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(batch) => Ok(Json(serde_json::json!(batch))),
        None => Err(StatusCode::NO_CONTENT),
    }
}

async fn batch_comparison(
    State(state): State<AppState>,
    Path(batch_id): Path<uuid::Uuid>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .comparison_for_batch(batch_id)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(batch) => Ok(Json(serde_json::json!(batch))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn run_report(
    State(state): State<AppState>,
    Path(run_id): Path<uuid::Uuid>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .run_report(run_id)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(report) => Ok(Json(serde_json::json!(report))),
        None => Err(StatusCode::NOT_FOUND),
    }
}

async fn run_journal(
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
