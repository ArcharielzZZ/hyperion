use hyperion_ingest::replay::replay_fixture;
use std::{env, path::PathBuf, process::ExitCode};

fn main() -> ExitCode {
    let Some(path) = env::args().nth(1) else {
        eprintln!("usage: cargo run -p hyperion-ingest --bin replay -- <fixture.ndjson>");
        return ExitCode::from(2);
    };

    match replay_fixture(&PathBuf::from(path)) {
        Ok(summary) => {
            println!(
                "messages={} parsed={} malformed={} duplicates={} ingested={}",
                summary.total_messages,
                summary.parsed_messages,
                summary.malformed_messages,
                summary.duplicate_records,
                summary.ingested_records
            );
            ExitCode::SUCCESS
        }
        Err(error) => {
            eprintln!("failed to replay fixture: {error}");
            ExitCode::from(1)
        }
    }
}
