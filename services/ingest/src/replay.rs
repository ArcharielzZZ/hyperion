use crate::{
    dedup::DedupCache,
    validation::{normalize_message, parse_message},
};
use chrono::Utc;
use std::{fs, path::Path};

#[derive(Debug, Default, Clone, Copy)]
pub struct ReplaySummary {
    pub total_messages: u64,
    pub parsed_messages: u64,
    pub malformed_messages: u64,
    pub duplicate_records: u64,
    pub ingested_records: u64,
}

pub fn replay_ndjson(contents: &str) -> ReplaySummary {
    let mut dedup = DedupCache::default();
    let mut summary = ReplaySummary::default();

    for line in contents
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
    {
        summary.total_messages += 1;

        let Ok(message) = parse_message(line) else {
            summary.malformed_messages += 1;
            continue;
        };
        summary.parsed_messages += 1;

        let Ok(batch) = normalize_message(&message, Utc::now()) else {
            summary.malformed_messages += 1;
            continue;
        };

        let (filtered, dedup_stats) = dedup.filter_batch(batch);
        summary.duplicate_records += dedup_stats.duplicates;
        summary.ingested_records += filtered.total_records();
    }

    summary
}

pub fn replay_fixture(path: &Path) -> std::io::Result<ReplaySummary> {
    let contents = fs::read_to_string(path)?;
    Ok(replay_ndjson(&contents))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replay_harness_deduplicates_reconnect_snapshots() {
        let fills = std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("fixtures/user_fills_snapshot.json"),
        )
        .expect("user fills fixture should exist");

        let replay_input = format!("{fills}\n{fills}\n");
        let summary = replay_ndjson(&replay_input);
        assert_eq!(summary.parsed_messages, 2);
        assert_eq!(summary.duplicate_records, 2);
        assert_eq!(summary.ingested_records, 2);
    }
}
