use crate::{health::router, hyperliquid, repository::Repository};
use anyhow::Result;
use hyperion_config::Settings;
use hyperion_utils::{wait_for_shutdown, Readiness, ServiceMetrics};
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;
use tokio::sync::watch;
use tracing::{error, info};

pub async fn run(settings: Settings) -> Result<()> {
    let pool = PgPoolOptions::new()
        .max_connections(10)
        .connect(&settings.database_url)
        .await?;

    let repository = Arc::new(Repository::new(pool, &settings.storage));
    let readiness = Readiness::new();
    readiness.mark_ready();
    let metrics = Arc::new(ServiceMetrics::default());
    let (shutdown_tx, shutdown_rx) = watch::channel(false);

    let listener = tokio::net::TcpListener::bind(settings.ingest_bind_addr).await?;
    info!(addr = %settings.ingest_bind_addr, "ingest health server listening");

    let app = router(readiness.clone(), metrics.clone());
    let server = tokio::spawn(async move { axum::serve(listener, app).await });

    let maintenance_repository = repository.clone();
    let storage_config = settings.storage.clone();
    let mut maintenance_shutdown_rx = shutdown_rx.clone();
    let maintenance_task = tokio::spawn(async move {
        let mut ticker = tokio::time::interval(storage_config.maintenance_interval);
        ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                _ = ticker.tick() => {
                    match maintenance_repository.run_storage_maintenance(&storage_config).await {
                        Ok(stats) => {
                            if stats.deleted_trade_ticks > 0 || stats.deleted_market_snapshots > 0 {
                                info!(
                                    deleted_trade_ticks = stats.deleted_trade_ticks,
                                    deleted_market_snapshots = stats.deleted_market_snapshots,
                                    "ingest storage maintenance pruned expired rows"
                                );
                            }
                        }
                        Err(error) => {
                            error!(?error, "ingest storage maintenance failed");
                        }
                    }
                }
                changed = maintenance_shutdown_rx.changed() => {
                    if changed.is_ok() && *maintenance_shutdown_rx.borrow() {
                        break;
                    }
                }
            }
        }
    });

    let ingest_task = tokio::spawn(hyperliquid::run_loop(
        settings.hyperliquid.clone(),
        repository,
        metrics,
        shutdown_rx,
    ));

    wait_for_shutdown().await;
    let _ = shutdown_tx.send(true);
    server.abort();
    let _ = maintenance_task.await;
    let _ = ingest_task.await?;
    Ok(())
}
