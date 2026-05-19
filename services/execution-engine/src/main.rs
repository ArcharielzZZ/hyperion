mod app;
mod engine;
mod repository;

use anyhow::Result;
use app::run;
use hyperion_config::Settings;
use hyperion_utils::init_tracing;

#[tokio::main]
async fn main() -> Result<()> {
    let settings = Settings::load()?;
    init_tracing(
        &settings.log_filter,
        settings.log_format.eq_ignore_ascii_case("json"),
    )?;
    run(settings).await
}
