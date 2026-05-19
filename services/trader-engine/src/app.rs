use crate::{engine, repository::Repository};
use anyhow::Result;
use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
    Json, Router,
};
use chrono::Utc;
use hyperion_config::Settings;
use hyperion_schemas::{HealthResponse, LeaderboardQuery};
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

    let run_once = std::env::var("HYPERION_TRADER_ENGINE_ONCE")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    if run_once {
        if let Err(error) = engine::recalculate(repository.clone()).await {
            error!(?error, "startup trader score recalculation failed");
        }
        info!("HYPERION_TRADER_ENGINE_ONCE set; exiting after single recalculation");
        return Ok(());
    }

    let startup_repository = repository.clone();
    tokio::spawn(async move {
        if let Err(error) = engine::recalculate(startup_repository).await {
            error!(?error, "startup trader score recalculation failed");
        }
    });
    readiness.mark_ready();

    let service_metrics = Arc::new(ServiceMetrics::default());
    let (shutdown_tx, mut shutdown_rx) = watch::channel(false);

    let scheduler_repository = repository.clone();
    let interval = settings.trader_engine.recalculation_interval;
    let scheduler = tokio::spawn(async move {
        loop {
            tokio::select! {
                _ = tokio::time::sleep(interval) => {
                    if let Err(error) = engine::recalculate(scheduler_repository.clone()).await {
                        error!(?error, "trader score recalculation failed");
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
        .route("/scores/leaderboard", get(leaderboard))
        .route("/traders/{wallet}", get(trader_detail))
        .with_state(AppState {
            repository,
            readiness,
            metrics: service_metrics,
        })
        .layer(TraceLayer::new_for_http());

    let listener = tokio::net::TcpListener::bind(settings.trader_engine_bind_addr).await?;
    info!(addr = %settings.trader_engine_bind_addr, "trader-engine listening");

    let server = tokio::spawn(async move { axum::serve(listener, app).await });

    wait_for_shutdown().await;
    let _ = shutdown_tx.send(true);
    scheduler.await?;
    server.abort();
    Ok(())
}

async fn live() -> Json<HealthResponse> {
    Json(HealthResponse {
        service: "trader-engine".to_string(),
        status: "up".to_string(),
        timestamp: Utc::now(),
    })
}

async fn ready(State(state): State<AppState>) -> impl IntoResponse {
    let status = if state.readiness.is_ready() {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (
        status,
        Json(HealthResponse {
            service: "trader-engine".to_string(),
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
    state.metrics.render_prometheus("trader-engine")
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

async fn trader_detail(
    State(state): State<AppState>,
    Path(wallet): Path<String>,
) -> Result<Json<serde_json::Value>, StatusCode> {
    match state
        .repository
        .trader_detail(&wallet)
        .await
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?
    {
        Some(detail) => Ok(Json(serde_json::json!(detail))),
        None => Err(StatusCode::NOT_FOUND),
    }
}
