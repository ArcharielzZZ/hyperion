use anyhow::Result;
use hyperion_config::Settings;
use hyperion_ingest::app::run;
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
