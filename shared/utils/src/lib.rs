use anyhow::Result;
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc,
    },
    time::Instant,
};
use tokio::signal;
use tracing_subscriber::{fmt, EnvFilter};

pub fn init_tracing(log_filter: &str, json: bool) -> Result<()> {
    let env_filter = EnvFilter::try_new(log_filter).unwrap_or_else(|_| EnvFilter::new("info"));
    let builder = fmt().with_env_filter(env_filter);
    if json {
        builder.json().init();
    } else {
        builder.pretty().init();
    }
    Ok(())
}

pub async fn wait_for_shutdown() {
    let _ = signal::ctrl_c().await;
}

#[derive(Clone, Default)]
pub struct Readiness {
    ready: Arc<AtomicBool>,
}

impl Readiness {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn mark_ready(&self) {
        self.ready.store(true, Ordering::Relaxed);
    }

    pub fn is_ready(&self) -> bool {
        self.ready.load(Ordering::Relaxed)
    }
}

pub struct ServiceMetrics {
    pub started_at: Instant,
    pub reconnects: AtomicU64,
    pub messages_received: AtomicU64,
    pub parse_failures: AtomicU64,
    pub malformed_messages: AtomicU64,
    pub db_write_failures: AtomicU64,
    pub duplicate_messages: AtomicU64,
    pub records_ingested: AtomicU64,
}

impl Default for ServiceMetrics {
    fn default() -> Self {
        Self {
            started_at: Instant::now(),
            reconnects: AtomicU64::new(0),
            messages_received: AtomicU64::new(0),
            parse_failures: AtomicU64::new(0),
            malformed_messages: AtomicU64::new(0),
            db_write_failures: AtomicU64::new(0),
            duplicate_messages: AtomicU64::new(0),
            records_ingested: AtomicU64::new(0),
        }
    }
}

impl ServiceMetrics {
    pub fn render_prometheus(&self, service: &str) -> String {
        let uptime_secs = self.started_at.elapsed().as_secs_f64().max(1.0);
        let records_ingested = self.records_ingested.load(Ordering::Relaxed);
        format!(
            concat!(
                "hyperion_service_info{{service=\"{service}\"}} 1\n",
                "hyperion_reconnects_total{{service=\"{service}\"}} {reconnects}\n",
                "hyperion_messages_received_total{{service=\"{service}\"}} {messages_received}\n",
                "hyperion_parse_failures_total{{service=\"{service}\"}} {parse_failures}\n",
                "hyperion_malformed_messages_total{{service=\"{service}\"}} {malformed_messages}\n",
                "hyperion_db_write_failures_total{{service=\"{service}\"}} {db_write_failures}\n",
                "hyperion_duplicate_messages_total{{service=\"{service}\"}} {duplicate_messages}\n",
                "hyperion_records_ingested_total{{service=\"{service}\"}} {records_ingested}\n",
                "hyperion_ingestion_throughput_per_sec{{service=\"{service}\"}} {throughput}\n"
            ),
            service = service,
            reconnects = self.reconnects.load(Ordering::Relaxed),
            messages_received = self.messages_received.load(Ordering::Relaxed),
            parse_failures = self.parse_failures.load(Ordering::Relaxed),
            malformed_messages = self.malformed_messages.load(Ordering::Relaxed),
            db_write_failures = self.db_write_failures.load(Ordering::Relaxed),
            duplicate_messages = self.duplicate_messages.load(Ordering::Relaxed),
            records_ingested = records_ingested,
            throughput = records_ingested as f64 / uptime_secs,
        )
    }
}
