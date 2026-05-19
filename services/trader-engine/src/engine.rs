use crate::{behavior, repository::Repository, scoring};
use anyhow::Result;
use chrono::Utc;
use std::{collections::HashSet, sync::Arc};
use tracing::info;

const MIN_SCORE_ACTIVE_DAYS: i64 = 1;
const MIN_SCORE_TRADE_OBSERVATIONS: i64 = 8;
const MIN_SCORE_PNL_SNAPSHOTS: i64 = 12;

pub async fn recalculate(repository: Arc<Repository>) -> Result<()> {
    let candidates = repository
        .fetch_score_candidates(
            MIN_SCORE_ACTIVE_DAYS,
            MIN_SCORE_TRADE_OBSERVATIONS,
            MIN_SCORE_PNL_SNAPSHOTS,
        )
        .await?;
    info!(count = candidates.len(), "recalculating trader scores");

    let as_of = Utc::now();
    let mut metrics = Vec::new();
    for candidate in candidates {
        info!(
            wallet = %candidate.wallet,
            fills_30d = candidate.fills_30d,
            positions_30d = candidate.positions_30d,
            pnl_snapshots_30d = candidate.pnl_snapshots_30d,
            active_days_30d = candidate.active_days_30d,
            "scoring eligible trader"
        );
        if let Some(input) = repository.fetch_behavior_input(candidate.trader_id).await? {
            let behavior = behavior::build_behavior_profile(&input, Utc::now());
            repository.insert_behavior_profile(behavior.profile).await?;
            for alert in behavior.alerts {
                repository.insert_behavior_alert(alert).await?;
            }

            if let Some(metric) = scoring::compute_wallet_metrics(&input, as_of) {
                metrics.push(metric);
            }
        }
    }

    let scores = scoring::score_wallets(&metrics, as_of);
    info!(count = scores.len(), "persisting normalized trader scores");
    for score in scores {
        repository.insert_score(score).await?;
    }

    let discovery_aggregates = repository.fetch_trader_discovery_aggregates().await?;
    let whale_wallets: HashSet<String> = repository
        .fetch_whale_registry_wallets()
        .await?
        .into_iter()
        .collect();
    info!(
        count = discovery_aggregates.len(),
        registry_whales = whale_wallets.len(),
        "updating trader discovery rankings"
    );
    for rank in scoring::build_discovery_ranks(&discovery_aggregates, as_of, &whale_wallets) {
        repository.upsert_discovery_rank(rank).await?;
    }

    Ok(())
}
