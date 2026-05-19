use crate::repository::{BehaviorInput, EntryTimingSample};
use chrono::{DateTime, Duration, Utc};
use hyperion_models::{BehavioralAlert, TraderBehaviorProfile};
use uuid::Uuid;

pub struct BehaviorComputation {
    pub profile: TraderBehaviorProfile,
    pub alerts: Vec<BehavioralAlert>,
}

pub fn build_behavior_profile(input: &BehaviorInput, as_of: DateTime<Utc>) -> BehaviorComputation {
    let fills_30d = input
        .fills
        .iter()
        .filter(|fill| fill.timestamp > as_of - Duration::days(30))
        .count() as i32;
    let fills_7d = input
        .fills
        .iter()
        .filter(|fill| fill.timestamp > as_of - Duration::days(7))
        .count() as i32;
    let pnl_values = input
        .pnl_snapshots
        .iter()
        .filter(|snapshot| snapshot.timestamp > as_of - Duration::days(30))
        .map(|snapshot| snapshot.realized_pnl + snapshot.unrealized_pnl)
        .collect::<Vec<_>>();
    let leverage_values = input
        .positions
        .iter()
        .filter(|position| position.timestamp > as_of - Duration::days(30))
        .map(|position| position.leverage.abs())
        .collect::<Vec<_>>();
    let entry_edges = entry_timing_edges(&input.entry_timing_samples);

    let avg_pnl_30d = mean(&pnl_values);
    let avg_pnl_7d = mean(
        &input
            .pnl_snapshots
            .iter()
            .filter(|snapshot| snapshot.timestamp > as_of - Duration::days(7))
            .map(|snapshot| snapshot.realized_pnl + snapshot.unrealized_pnl)
            .collect::<Vec<_>>(),
    );
    let pnl_vol_30d = stddev(&pnl_values);
    let pnl_vol_7d = pnl_vol_30d;
    let leverage_avg_30d = mean(&leverage_values);
    let leverage_peak_30d = leverage_values.iter().copied().fold(0.0, f64::max);

    let profile = TraderBehaviorProfile {
        id: Uuid::new_v4(),
        trader_id: input.trader.id,
        rolling_consistency_score_7d: bounded(100.0 - pnl_vol_7d.abs()),
        rolling_consistency_score_30d: bounded(100.0 - pnl_vol_30d.abs()),
        avg_daily_pnl_7d: avg_pnl_7d,
        avg_daily_pnl_30d: avg_pnl_30d,
        pnl_volatility_7d: pnl_vol_7d,
        pnl_volatility_30d: pnl_vol_30d,
        win_rate_30d: positive_rate(&pnl_values) * 100.0,
        recent_leverage_avg_7d: leverage_avg_30d,
        leverage_avg_30d,
        leverage_peak_30d,
        leverage_volatility_30d: stddev(&leverage_values),
        max_drawdown_pct_30d: drawdown_pct(&pnl_values),
        avg_hold_duration_secs_30d: 0.0,
        median_hold_duration_secs_30d: 0.0,
        entry_timing_edge_bps_30d: mean(&entry_edges),
        favorable_entry_rate_30d: positive_rate(&entry_edges) * 100.0,
        behavioral_drift_score: bounded((pnl_vol_30d - avg_pnl_30d).abs()),
        emotional_volatility_score: bounded(pnl_vol_30d),
        revenge_trading_score: bounded(fills_7d as f64 * 2.0),
        sizing_instability_score: bounded(stddev(
            &input.fills.iter().map(|fill| fill.size).collect::<Vec<_>>(),
        )),
        consistency_score_delta: bounded(avg_pnl_7d - avg_pnl_30d),
        active_days_30d: fills_30d.min(30),
        fills_7d,
        fills_30d,
        lifecycle_stage: if fills_7d > 0 {
            "active".to_string()
        } else {
            "dormant".to_string()
        },
        window_start: as_of - Duration::days(30),
        window_end: as_of,
        timestamp: as_of,
        created_at: as_of,
    };

    let alerts = build_alerts(&profile, as_of);
    BehaviorComputation { profile, alerts }
}

fn build_alerts(profile: &TraderBehaviorProfile, as_of: DateTime<Utc>) -> Vec<BehavioralAlert> {
    let mut alerts = Vec::new();
    if profile.leverage_peak_30d > 5.0 {
        alerts.push(new_alert(
            profile.trader_id,
            "leverage_spike_detected",
            "warning",
            "Leverage Spike",
            "Recent leverage exceeded the configured risk comfort zone.",
            profile.leverage_peak_30d,
            5.0,
            as_of,
        ));
    }
    if profile.emotional_volatility_score > 25.0 {
        alerts.push(new_alert(
            profile.trader_id,
            "increasing_emotional_volatility",
            "warning",
            "Emotional Volatility",
            "PnL variance has increased materially over the evaluation window.",
            profile.emotional_volatility_score,
            25.0,
            as_of,
        ));
    }
    alerts
}

fn new_alert(
    trader_id: Uuid,
    alert_type: &str,
    severity: &str,
    title: &str,
    message: &str,
    metric_value: f64,
    threshold_value: f64,
    detected_at: DateTime<Utc>,
) -> BehavioralAlert {
    BehavioralAlert {
        id: Uuid::new_v4(),
        trader_id,
        alert_type: alert_type.to_string(),
        severity: severity.to_string(),
        title: title.to_string(),
        message: message.to_string(),
        metric_value,
        threshold_value,
        detected_at,
        dedupe_key: format!("{}:{}:{}", trader_id, alert_type, detected_at.date_naive()),
        created_at: detected_at,
    }
}

fn entry_timing_edges(samples: &[EntryTimingSample]) -> Vec<f64> {
    samples
        .iter()
        .filter_map(|sample| {
            sample
                .future_price
                .map(|future_price| match sample.side.as_str() {
                    "buy" => ((future_price / sample.fill_price.max(1.0)) - 1.0) * 10_000.0,
                    "sell" => {
                        ((sample.fill_price.max(1.0) / future_price.max(1.0)) - 1.0) * 10_000.0
                    }
                    _ => 0.0,
                })
        })
        .collect()
}

fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().sum::<f64>() / values.len() as f64
    }
}

fn stddev(values: &[f64]) -> f64 {
    if values.len() < 2 {
        return 0.0;
    }
    let avg = mean(values);
    let variance = values
        .iter()
        .map(|value| {
            let delta = *value - avg;
            delta * delta
        })
        .sum::<f64>()
        / (values.len() as f64 - 1.0);
    variance.sqrt()
}

fn positive_rate(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().filter(|value| **value > 0.0).count() as f64 / values.len() as f64
    }
}

fn drawdown_pct(values: &[f64]) -> f64 {
    let mut peak: f64 = 0.0;
    let mut max_drawdown: f64 = 0.0;
    for value in values {
        peak = peak.max(*value);
        if peak > 0.0 {
            max_drawdown = max_drawdown.max(((peak - *value) / peak) * 100.0);
        }
    }
    max_drawdown
}

fn bounded(value: f64) -> f64 {
    value.clamp(0.0, 100.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_leverage_spike_alert() {
        let now = Utc::now();
        let profile = TraderBehaviorProfile {
            id: Uuid::new_v4(),
            trader_id: Uuid::new_v4(),
            rolling_consistency_score_7d: 0.0,
            rolling_consistency_score_30d: 0.0,
            avg_daily_pnl_7d: 0.0,
            avg_daily_pnl_30d: 0.0,
            pnl_volatility_7d: 0.0,
            pnl_volatility_30d: 0.0,
            win_rate_30d: 0.0,
            recent_leverage_avg_7d: 0.0,
            leverage_avg_30d: 0.0,
            leverage_peak_30d: 6.0,
            leverage_volatility_30d: 0.0,
            max_drawdown_pct_30d: 0.0,
            avg_hold_duration_secs_30d: 0.0,
            median_hold_duration_secs_30d: 0.0,
            entry_timing_edge_bps_30d: 0.0,
            favorable_entry_rate_30d: 0.0,
            behavioral_drift_score: 0.0,
            emotional_volatility_score: 0.0,
            revenge_trading_score: 0.0,
            sizing_instability_score: 0.0,
            consistency_score_delta: 0.0,
            active_days_30d: 0,
            fills_7d: 0,
            fills_30d: 0,
            lifecycle_stage: "active".to_string(),
            window_start: now,
            window_end: now,
            timestamp: now,
            created_at: now,
        };
        let alerts = build_alerts(&profile, now);
        assert!(!alerts.is_empty());
    }
}
