use crate::repository::{Repository, SignalCandidate};
use anyhow::Result;
use chrono::{Duration, Utc};
use hyperion_config::SignalEngineConfig;
use hyperion_models::{Signal, SignalOutcome};
use std::sync::Arc;
use tracing::info;
use uuid::Uuid;

pub async fn recalculate(repository: Arc<Repository>, config: &SignalEngineConfig) -> Result<()> {
    let candidates = repository
        .candidate_consensus(
            config.min_score,
            config.min_confirmations,
            config.signal_window_secs,
        )
        .await?;

    info!(count = candidates.len(), "evaluating signal candidates");

    for candidate in candidates {
        let contributors = repository
            .candidate_contributors(&candidate, config.min_score, config.signal_window_secs)
            .await?;
        let regime = classify_regime(&candidate);
        let momentum_score = momentum_score(&candidate);
        let volatility_score = volatility_score(&candidate);
        let confidence_score = confidence_score(&candidate, config.min_confirmations);

        if !passes_filters(momentum_score, volatility_score) {
            continue;
        }

        let market_context = serde_json::json!({
            "funding_rate": candidate.funding_rate,
            "open_interest": candidate.open_interest,
            "reference_price": candidate.reference_price,
            "momentum_5m_bps": candidate.momentum_5m_bps,
            "momentum_15m_bps": candidate.momentum_15m_bps,
            "volatility_1h_bps": candidate.volatility_1h_bps,
            "volatility_regime": volatility_regime(&candidate),
        })
        .to_string();

        let rationale = format!(
            "{} traders aligned on {} {} inside {}s window ending {} with weighted quality {:.2}, momentum {:.1}, volatility {:.1}",
            candidate.supporting_traders,
            candidate.coin,
            candidate.direction,
            config.signal_window_secs,
            candidate.latest_entry_timestamp,
            candidate.quality_weight,
            momentum_score,
            volatility_score,
        );

        let signal = Signal {
            id: Uuid::new_v4(),
            coin: candidate.coin.clone(),
            direction: candidate.direction.clone(),
            score: confidence_score,
            confidence_score,
            supporting_traders: candidate.supporting_traders as i32,
            quality_weight: candidate.quality_weight,
            momentum_score,
            volatility_score,
            regime,
            market_context,
            rationale,
            status: "active".to_string(),
            consensus_window_key: format!(
                "{}|{}|{}",
                candidate.coin, candidate.direction, candidate.window_start
            ),
            signal_window_start: candidate.window_start,
            signal_window_end: candidate.window_end,
            activated_at: Utc::now(),
            expires_at: candidate.window_end + Duration::minutes(config.signal_expiry_mins),
            resolved_at: None,
            reference_price: candidate.reference_price,
            timestamp: Utc::now(),
            created_at: Utc::now(),
        };

        repository.upsert_signal(&signal, &contributors).await?;
    }

    repository.mark_expired_signals().await?;

    let horizons = [60, config.signal_expiry_mins as i32];
    for (index, horizon) in horizons.iter().enumerate() {
        let due = repository.due_outcomes(*horizon).await?;
        for pending in due {
            let Some(window) = repository
                .price_window_stats(&pending.coin, pending.signal_window_end, *horizon)
                .await?
            else {
                continue;
            };

            let realized_return_bps = directional_return_bps(
                &pending.direction,
                pending.reference_price,
                window.exit_price,
            );
            let max_favorable_excursion_bps = favorable_excursion_bps(
                &pending.direction,
                pending.reference_price,
                window.max_price,
                window.min_price,
            );
            let max_adverse_excursion_bps = adverse_excursion_bps(
                &pending.direction,
                pending.reference_price,
                window.max_price,
                window.min_price,
            );

            repository
                .insert_signal_outcome(
                    &SignalOutcome {
                        id: Uuid::new_v4(),
                        signal_id: pending.signal_id,
                        horizon_minutes: *horizon,
                        entry_price: pending.reference_price,
                        exit_price: window.exit_price,
                        realized_return_bps,
                        max_favorable_excursion_bps,
                        max_adverse_excursion_bps,
                        outcome_label: outcome_label(realized_return_bps).to_string(),
                        resolved_at: window.resolved_at,
                        created_at: Utc::now(),
                    },
                    index == horizons.len() - 1,
                )
                .await?;
        }
    }

    Ok(())
}

fn classify_regime(candidate: &SignalCandidate) -> String {
    if candidate.open_interest > 0.0
        && candidate.funding_rate.abs() < 0.001
        && candidate.momentum_15m_bps.abs() > 12.0
    {
        "trend_expansion".to_string()
    } else if candidate.funding_rate.abs() >= 0.0015 {
        "crowded".to_string()
    } else if candidate.volatility_1h_bps > 90.0 {
        "volatile".to_string()
    } else {
        "balanced".to_string()
    }
}

fn momentum_score(candidate: &SignalCandidate) -> f64 {
    let directional_momentum = match candidate.direction.as_str() {
        "long" => (candidate.momentum_5m_bps + candidate.momentum_15m_bps) / 2.0,
        "short" => ((-candidate.momentum_5m_bps) + (-candidate.momentum_15m_bps)) / 2.0,
        _ => 0.0,
    };
    ((directional_momentum / 25.0).clamp(-1.0, 1.0) + 1.0) * 50.0
}

fn volatility_regime(candidate: &SignalCandidate) -> &'static str {
    if candidate.volatility_1h_bps >= 150.0 {
        "extreme"
    } else if candidate.volatility_1h_bps >= 90.0 {
        "high"
    } else if candidate.volatility_1h_bps <= 20.0 {
        "low"
    } else {
        "normal"
    }
}

fn volatility_score(candidate: &SignalCandidate) -> f64 {
    match volatility_regime(candidate) {
        "normal" => 85.0,
        "low" => 70.0,
        "high" => 45.0,
        "extreme" => 20.0,
        _ => 50.0,
    }
}

fn confidence_score(candidate: &SignalCandidate, min_confirmations: usize) -> f64 {
    let consensus_component =
        ((candidate.supporting_traders as f64 / min_confirmations.max(1) as f64).min(2.0) / 2.0)
            * 100.0;
    let quality_component = (candidate.avg_score / 100.0).clamp(0.0, 1.0) * 100.0;
    let momentum_component = momentum_score(candidate);
    let volatility_component = volatility_score(candidate);
    (consensus_component * 0.25
        + quality_component * 0.40
        + momentum_component * 0.20
        + volatility_component * 0.15)
        .clamp(0.0, 100.0)
}

fn passes_filters(momentum_score: f64, volatility_score: f64) -> bool {
    momentum_score >= 45.0 && volatility_score >= 25.0
}

fn directional_return_bps(direction: &str, entry_price: f64, exit_price: f64) -> f64 {
    if entry_price <= 0.0 {
        return 0.0;
    }
    match direction {
        "long" => ((exit_price / entry_price) - 1.0) * 10_000.0,
        "short" => ((entry_price / exit_price) - 1.0) * 10_000.0,
        _ => 0.0,
    }
}

fn favorable_excursion_bps(
    direction: &str,
    entry_price: f64,
    max_price: f64,
    min_price: f64,
) -> f64 {
    match direction {
        "long" => directional_return_bps(direction, entry_price, max_price).max(0.0),
        "short" => directional_return_bps(direction, entry_price, min_price).max(0.0),
        _ => 0.0,
    }
}

fn adverse_excursion_bps(direction: &str, entry_price: f64, max_price: f64, min_price: f64) -> f64 {
    match direction {
        "long" => directional_return_bps(direction, entry_price, min_price).min(0.0),
        "short" => directional_return_bps(direction, entry_price, max_price).min(0.0),
        _ => 0.0,
    }
}

fn outcome_label(realized_return_bps: f64) -> &'static str {
    if realized_return_bps >= 35.0 {
        "strong_win"
    } else if realized_return_bps > 0.0 {
        "win"
    } else if realized_return_bps <= -35.0 {
        "loss"
    } else {
        "flat"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn candidate(
        direction: &str,
        momentum_5m_bps: f64,
        momentum_15m_bps: f64,
        volatility_1h_bps: f64,
    ) -> SignalCandidate {
        SignalCandidate {
            coin: "BTC".to_string(),
            direction: direction.to_string(),
            supporting_traders: 4,
            avg_score: 82.0,
            quality_weight: 3.1,
            funding_rate: 0.0001,
            open_interest: 10_000.0,
            reference_price: 80_000.0,
            momentum_5m_bps,
            momentum_15m_bps,
            volatility_1h_bps,
            latest_entry_timestamp: Utc::now(),
            window_start: Utc::now(),
            window_end: Utc::now(),
        }
    }

    #[test]
    fn long_signals_reward_positive_momentum() {
        let aligned = candidate("long", 18.0, 32.0, 40.0);
        let opposed = candidate("long", -18.0, -32.0, 40.0);
        assert!(momentum_score(&aligned) > momentum_score(&opposed));
        assert!(confidence_score(&aligned, 3) > confidence_score(&opposed, 3));
    }

    #[test]
    fn extreme_volatility_fails_filter() {
        let candidate = candidate("long", 20.0, 25.0, 180.0);
        assert!(!passes_filters(
            momentum_score(&candidate),
            volatility_score(&candidate)
        ));
    }
}
