use axum::{extract::State, http::StatusCode, routing::get, Json, Router};
use chrono::Utc;
use hyperion_schemas::HealthResponse;
use hyperion_utils::{Readiness, ServiceMetrics};
use std::sync::Arc;

#[derive(Clone)]
struct AppState {
    readiness: Readiness,
    metrics: Arc<ServiceMetrics>,
}

pub fn router(readiness: Readiness, metrics: Arc<ServiceMetrics>) -> Router {
    Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/metrics", get(metrics_endpoint))
        .with_state(AppState { readiness, metrics })
}

async fn live() -> Json<HealthResponse> {
    Json(HealthResponse {
        service: "ingest".to_string(),
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
            service: "ingest".to_string(),
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
    state.metrics.render_prometheus("ingest")
}
