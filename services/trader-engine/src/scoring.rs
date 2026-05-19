use crate::repository::{BehaviorInput, TraderDiscoveryAggregate};
use chrono::{DateTime, Duration, NaiveDate, Utc};
use hyperion_models::{TraderDiscoveryRank, TraderScore};
use std::{
    cmp::Ordering,
    collections::{BTreeMap, HashMap, HashSet},
};
use uuid::Uuid;

const EPSILON: f64 = 1e-9;

#[derive(Debug, Clone)]
pub struct WalletQuantMetrics {
    pub trader_id: Uuid,
    pub active_days_30d: usize,
    pub fills_30d: usize,
    pub positions_30d: usize,
    pub daily_return_count: usize,
    pub leverage_count: usize,
    pub entry_edge_count: usize,
    pub sharpe_like: f64,
    pub return_vol_bps: f64,
    pub downside_vol_bps: f64,
    pub hit_rate_pct: f64,
    pub max_drawdown_pct: f64,
    pub recovery_factor: f64,
    pub entry_timing_edge_bps: f64,
    pub favorable_entry_rate_pct: f64,
    pub avg_leverage: f64,
    pub max_leverage: f64,
    pub leverage_volatility: f64,
    pub sizing_cv: f64,
    pub expectancy_bps: f64,
    pub profit_factor: f64,
    pub avg_holding_hours: f64,
}

pub fn compute_wallet_metrics(
    input: &BehaviorInput,
    as_of: DateTime<Utc>,
) -> Option<WalletQuantMetrics> {
    let fills_30d = input
        .fills
        .iter()
        .filter(|fill| fill.timestamp > as_of - Duration::days(30))
        .cloned()
        .collect::<Vec<_>>();
    let positions_30d = input
        .positions
        .iter()
        .filter(|position| position.timestamp > as_of - Duration::days(30))
        .cloned()
        .collect::<Vec<_>>();
    let pnl_30d = input
        .pnl_snapshots
        .iter()
        .filter(|snapshot| snapshot.timestamp > as_of - Duration::days(30))
        .cloned()
        .collect::<Vec<_>>();

    let active_days_30d = distinct_active_days(&fills_30d, &positions_30d, &pnl_30d);
    if active_days_30d == 0 {
        return None;
    }

    let equity_curve = hourly_equity_curve(&pnl_30d);
    let daily_returns_bps = equity_returns_bps(&equity_curve);
    let leverage_values = positions_30d
        .iter()
        .map(|position| position.leverage.abs())
        .collect::<Vec<_>>();
    let fill_notionals = fills_30d
        .iter()
        .map(|fill| (fill.size.abs() * fill.price.abs()).max(0.0))
        .filter(|value| *value > 0.0)
        .collect::<Vec<_>>();
    let entry_edges = input
        .entry_timing_samples
        .iter()
        .filter_map(|sample| {
            (sample.fill_timestamp > as_of - Duration::days(30))
                .then(|| entry_edge_bps(&sample.side, sample.fill_price, sample.future_price))
        })
        .flatten()
        .collect::<Vec<_>>();

    let sharpe_like = sharpe_like(&daily_returns_bps);
    let return_vol_bps = stddev(&daily_returns_bps);
    let downside_vol_bps = downside_stddev(&daily_returns_bps);
    let hit_rate_pct = positive_rate(&daily_returns_bps) * 100.0;
    let max_drawdown_pct = max_drawdown_pct(&equity_curve);
    let recovery_factor = recovery_factor(&equity_curve, max_drawdown_pct);
    let entry_timing_edge_bps = mean(&entry_edges);
    let favorable_entry_rate_pct = positive_rate(&entry_edges) * 100.0;
    let avg_leverage = mean(&leverage_values);
    let max_leverage = leverage_values.iter().copied().fold(0.0, f64::max);
    let leverage_volatility = stddev(&leverage_values);
    let sizing_cv = coefficient_of_variation(&fill_notionals);
    let expectancy_bps = mean(&daily_returns_bps);
    let profit_factor = profit_factor(&daily_returns_bps);
    let avg_holding_hours = mean(&holding_durations_hours(&positions_30d));

    Some(WalletQuantMetrics {
        trader_id: input.trader.id,
        active_days_30d,
        fills_30d: fills_30d.len(),
        positions_30d: positions_30d.len(),
        daily_return_count: daily_returns_bps.len(),
        leverage_count: leverage_values.len(),
        entry_edge_count: entry_edges.len(),
        sharpe_like,
        return_vol_bps,
        downside_vol_bps,
        hit_rate_pct,
        max_drawdown_pct,
        recovery_factor,
        entry_timing_edge_bps,
        favorable_entry_rate_pct,
        avg_leverage,
        max_leverage,
        leverage_volatility,
        sizing_cv,
        expectancy_bps,
        profit_factor,
        avg_holding_hours,
    })
}

pub fn score_wallets(metrics: &[WalletQuantMetrics], as_of: DateTime<Utc>) -> Vec<TraderScore> {
    if metrics.is_empty() {
        return Vec::new();
    }

    let sharpe_pct = percentile_map(metrics, |item| item.sharpe_like, true);
    let return_vol_pct = percentile_map(metrics, |item| item.return_vol_bps, false);
    let downside_vol_pct = percentile_map(metrics, |item| item.downside_vol_bps, false);
    let hit_rate_pct = percentile_map(metrics, |item| item.hit_rate_pct, true);
    let drawdown_pct = percentile_map(metrics, |item| item.max_drawdown_pct, false);
    let recovery_pct = percentile_map(metrics, |item| item.recovery_factor, true);
    let active_days_pct = percentile_map(metrics, |item| item.active_days_30d as f64, true);
    let entry_edge_pct = percentile_map(metrics, |item| item.entry_timing_edge_bps, true);
    let favorable_entry_pct = percentile_map(metrics, |item| item.favorable_entry_rate_pct, true);
    let max_leverage_pct = percentile_map(metrics, |item| item.max_leverage, false);
    let avg_leverage_pct = percentile_map(metrics, |item| item.avg_leverage, false);
    let leverage_vol_pct = percentile_map(metrics, |item| item.leverage_volatility, false);
    let sizing_cv_pct = percentile_map(metrics, |item| item.sizing_cv, false);
    let expectancy_pct = percentile_map(metrics, |item| item.expectancy_bps, true);
    let profit_factor_pct = percentile_map(metrics, |item| item.profit_factor, true);
    let fills_pct = percentile_map(metrics, |item| item.fills_30d as f64, true);

    metrics
        .iter()
        .map(|item| {
            let consistency_confidence = evidence_confidence(item.daily_return_count, 20);
            let survivability_confidence = evidence_confidence(item.active_days_30d, 10)
                .min(evidence_confidence(item.daily_return_count, 20));
            let timing_confidence = evidence_confidence(item.entry_edge_count, 15);
            let leverage_confidence = evidence_confidence(item.leverage_count, 20);
            let conviction_confidence = evidence_confidence(item.daily_return_count, 20)
                .min(evidence_confidence(item.fills_30d + item.positions_30d, 20));

            let consistency_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&sharpe_pct, item.trader_id), 0.40),
                    (score_of(&return_vol_pct, item.trader_id), 0.20),
                    (score_of(&downside_vol_pct, item.trader_id), 0.20),
                    (score_of(&hit_rate_pct, item.trader_id), 0.20),
                ]),
                consistency_confidence,
            );
            let survivability_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&drawdown_pct, item.trader_id), 0.50),
                    (score_of(&recovery_pct, item.trader_id), 0.30),
                    (score_of(&active_days_pct, item.trader_id), 0.20),
                ]),
                survivability_confidence,
            );
            let timing_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&entry_edge_pct, item.trader_id), 0.65),
                    (score_of(&favorable_entry_pct, item.trader_id), 0.35),
                ]),
                timing_confidence,
            );
            let leverage_discipline_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&max_leverage_pct, item.trader_id), 0.40),
                    (score_of(&avg_leverage_pct, item.trader_id), 0.35),
                    (score_of(&leverage_vol_pct, item.trader_id), 0.15),
                    (score_of(&sizing_cv_pct, item.trader_id), 0.10),
                ]),
                leverage_confidence,
            );
            let conviction_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&expectancy_pct, item.trader_id), 0.40),
                    (score_of(&profit_factor_pct, item.trader_id), 0.35),
                    (score_of(&active_days_pct, item.trader_id), 0.15),
                    (score_of(&fills_pct, item.trader_id), 0.10),
                ]),
                conviction_confidence,
            );
            let total_score = bounded(weighted_average(&[
                (consistency_score, 0.30),
                (survivability_score, 0.25),
                (timing_score, 0.15),
                (leverage_discipline_score, 0.15),
                (conviction_score, 0.15),
            ]));

            TraderScore {
                id: Uuid::new_v4(),
                trader_id: item.trader_id,
                consistency_score,
                survivability_score,
                timing_score,
                leverage_discipline_score,
                conviction_score,
                total_score,
                style: classify_style(item).to_string(),
                timestamp: as_of,
                created_at: as_of,
            }
        })
        .collect()
}

pub fn build_discovery_ranks(
    aggregates: &[TraderDiscoveryAggregate],
    as_of: DateTime<Utc>,
    whale_wallets: &HashSet<String>,
) -> Vec<TraderDiscoveryRank> {
    if aggregates.is_empty() && whale_wallets.is_empty() {
        return Vec::new();
    }

    let trade_count_pct =
        percentile_map(aggregates, |item| item.public_trade_count_24h as f64, true);
    let notional_pct = percentile_map(aggregates, |item| item.public_notional_usd_24h, true);
    let breadth_pct = percentile_map(aggregates, |item| item.active_coins_24h as f64, true);
    let freshness_pct = percentile_map(
        aggregates,
        |item| age_minutes(item.last_public_trade_at, as_of),
        false,
    );
    let fills_pct = percentile_map(aggregates, |item| item.fills_24h as f64, true);
    let positions_pct = percentile_map(aggregates, |item| item.positions_24h as f64, true);
    let pnl_pct = percentile_map(aggregates, |item| item.pnl_snapshots_24h as f64, true);
    let behavior_pct = percentile_map(
        aggregates,
        |item| item.latest_behavior_score.unwrap_or(0.0),
        true,
    );

    aggregates
        .iter()
        .map(|item| {
            let wallet_key = item.wallet.to_lowercase();
            let is_registry_whale = whale_wallets.contains(&wallet_key);
            let filter_evaluated = item.wallet_filter_updated_at.is_some();

            // After wallet-filter batch: promotion follows traders.wallet_status only.
            // Promotion requires wallet-filter evaluation; legacy auto-promote is disabled post-0014.
            let mut promoted = if filter_evaluated {
                item.wallet_status.as_deref() == Some("PROMOTED")
            } else {
                false
            };
            let public_confidence = evidence_confidence(item.public_trade_count_24h as usize, 25);
            let coverage_confidence = evidence_confidence(
                (item.fills_24h + item.positions_24h + item.pnl_snapshots_24h) as usize,
                20,
            );

            let activity_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&trade_count_pct, item.trader_id), 0.35),
                    (score_of(&notional_pct, item.trader_id), 0.30),
                    (score_of(&breadth_pct, item.trader_id), 0.20),
                    (score_of(&freshness_pct, item.trader_id), 0.15),
                ]),
                public_confidence,
            );
            let data_coverage_score = shrink_to_neutral(
                weighted_average(&[
                    (score_of(&fills_pct, item.trader_id), 0.25),
                    (score_of(&positions_pct, item.trader_id), 0.25),
                    (score_of(&pnl_pct, item.trader_id), 0.25),
                    (score_of(&behavior_pct, item.trader_id), 0.25),
                ]),
                coverage_confidence,
            );
            let discovery_score = bounded(weighted_average(&[
                (activity_score, 0.55),
                (data_coverage_score, 0.45),
            ]));
            if is_registry_whale && !filter_evaluated {
                promoted = true;
            }
            let rank_tier = if let Some(tier) = item.behavior_tier.as_ref().filter(|t| !t.is_empty()) {
                tier.clone()
            } else if is_registry_whale {
                "whale".to_string()
            } else if promoted && discovery_score >= 65.0 {
                "active_tracked".to_string()
            } else if discovery_score >= 75.0 {
                "high_quality".to_string()
            } else if discovery_score >= 50.0 {
                "watchlist".to_string()
            } else {
                "watchlist".to_string()
            };

            TraderDiscoveryRank {
                id: Uuid::new_v4(),
                trader_id: item.trader_id,
                wallet: item.wallet.clone(),
                public_trade_count_1h: item.public_trade_count_1h.clamp(0, i32::MAX as i64) as i32,
                public_trade_count_24h: item.public_trade_count_24h.clamp(0, i32::MAX as i64)
                    as i32,
                public_notional_usd_1h: item.public_notional_usd_1h,
                public_notional_usd_24h: item.public_notional_usd_24h,
                active_coins_24h: item.active_coins_24h.clamp(0, i32::MAX as i64) as i32,
                buy_ratio_24h: item.buy_ratio_24h,
                fills_24h: item.fills_24h.clamp(0, i32::MAX as i64) as i32,
                positions_24h: item.positions_24h.clamp(0, i32::MAX as i64) as i32,
                pnl_snapshots_24h: item.pnl_snapshots_24h.clamp(0, i32::MAX as i64) as i32,
                last_public_trade_at: item.last_public_trade_at,
                latest_behavior_score: item.latest_behavior_score.unwrap_or(0.0),
                data_coverage_score,
                activity_score,
                discovery_score,
                rank_tier,
                promoted,
                timestamp: as_of,
                created_at: as_of,
            }
        })
        .collect()
}

fn classify_style(item: &WalletQuantMetrics) -> &'static str {
    if item.avg_leverage >= 2.5 && item.fills_30d >= 50 && item.avg_holding_hours <= 8.0 {
        "scalp"
    } else if item.entry_timing_edge_bps > 10.0 && item.expectancy_bps > 0.0 {
        "momentum"
    } else if item.entry_timing_edge_bps < -5.0 && item.expectancy_bps > 0.0 {
        "mean_reversion"
    } else {
        "swing"
    }
}

fn distinct_active_days(
    fills: &[hyperion_models::Fill],
    positions: &[hyperion_models::Position],
    pnl_snapshots: &[hyperion_models::PnlSnapshot],
) -> usize {
    let mut days = BTreeMap::<NaiveDate, ()>::new();
    for timestamp in fills
        .iter()
        .map(|fill| fill.timestamp)
        .chain(positions.iter().map(|position| position.timestamp))
        .chain(pnl_snapshots.iter().map(|snapshot| snapshot.timestamp))
    {
        days.insert(timestamp.date_naive(), ());
    }
    days.len()
}

fn hourly_equity_curve(snapshots: &[hyperion_models::PnlSnapshot]) -> Vec<(i64, f64)> {
    let mut by_bucket = BTreeMap::<i64, (DateTime<Utc>, f64)>::new();
    for snapshot in snapshots {
        let bucket = snapshot.timestamp.timestamp() / 3600;
        match by_bucket.get(&bucket) {
            Some((latest_ts, _)) if *latest_ts >= snapshot.timestamp => {}
            _ => {
                by_bucket.insert(bucket, (snapshot.timestamp, snapshot.equity));
            }
        }
    }

    by_bucket
        .into_iter()
        .map(|(bucket, (_, equity))| (bucket, equity))
        .collect()
}

fn equity_returns_bps(curve: &[(i64, f64)]) -> Vec<f64> {
    curve
        .windows(2)
        .filter_map(|window| {
            let previous = window[0].1;
            let current = window[1].1;
            (previous.abs() > EPSILON).then_some(((current / previous) - 1.0) * 10_000.0)
        })
        .collect()
}

fn max_drawdown_pct(curve: &[(i64, f64)]) -> f64 {
    let mut peak: f64 = 0.0;
    let mut drawdown: f64 = 0.0;
    for (_, equity) in curve {
        peak = peak.max(*equity);
        if peak > EPSILON {
            drawdown = drawdown.max(((peak - *equity) / peak) * 100.0);
        }
    }
    drawdown
}

fn recovery_factor(curve: &[(i64, f64)], max_drawdown_pct: f64) -> f64 {
    if curve.len() < 2 {
        return 0.0;
    }
    let start = curve.first().map(|(_, equity)| *equity).unwrap_or(0.0);
    let end = curve.last().map(|(_, equity)| *equity).unwrap_or(0.0);
    if start.abs() <= EPSILON {
        return 0.0;
    }
    let total_return_pct = ((end / start) - 1.0) * 100.0;
    total_return_pct / max_drawdown_pct.max(1.0)
}

fn entry_edge_bps(side: &str, fill_price: f64, future_price: Option<f64>) -> Option<f64> {
    let future_price = future_price?;
    let denominator = fill_price.abs().max(1.0);
    let edge = match side {
        "buy" => ((future_price / denominator) - 1.0) * 10_000.0,
        "sell" => ((denominator / future_price.abs().max(1.0)) - 1.0) * 10_000.0,
        _ => return None,
    };
    Some(edge)
}

fn holding_durations_hours(positions: &[hyperion_models::Position]) -> Vec<f64> {
    let mut grouped = BTreeMap::<(String, String), Vec<DateTime<Utc>>>::new();
    for position in positions {
        grouped
            .entry((position.coin.clone(), position.direction.clone()))
            .or_default()
            .push(position.timestamp);
    }

    let mut durations = Vec::new();
    for timestamps in grouped.values_mut() {
        timestamps.sort();
        let mut segment_start = None;
        let mut last = None;
        for timestamp in timestamps.iter().copied() {
            match (segment_start, last) {
                (None, _) => {
                    segment_start = Some(timestamp);
                    last = Some(timestamp);
                }
                (Some(start), Some(previous)) => {
                    if timestamp - previous > Duration::minutes(45) {
                        durations.push(((previous - start).num_seconds() as f64 / 3600.0).max(0.0));
                        segment_start = Some(timestamp);
                    }
                    last = Some(timestamp);
                }
                _ => {}
            }
        }
        if let (Some(start), Some(end)) = (segment_start, last) {
            durations.push(((end - start).num_seconds() as f64 / 3600.0).max(0.0));
        }
    }

    durations
}

fn profit_factor(returns_bps: &[f64]) -> f64 {
    let gains = returns_bps
        .iter()
        .copied()
        .filter(|value| *value > 0.0)
        .sum::<f64>();
    let losses = returns_bps
        .iter()
        .copied()
        .filter(|value| *value < 0.0)
        .sum::<f64>()
        .abs();

    if losses <= EPSILON {
        if gains > EPSILON {
            5.0
        } else {
            1.0
        }
    } else {
        gains / losses
    }
}

fn sharpe_like(values: &[f64]) -> f64 {
    let volatility = stddev(values);
    if volatility <= EPSILON {
        return 0.0;
    }
    mean(values) / volatility
}

fn coefficient_of_variation(values: &[f64]) -> f64 {
    let average = mean(values).abs();
    if average <= EPSILON {
        return 0.0;
    }
    stddev(values) / average
}

fn downside_stddev(values: &[f64]) -> f64 {
    let negatives = values
        .iter()
        .copied()
        .filter(|value| *value < 0.0)
        .collect::<Vec<_>>();
    stddev(&negatives)
}

fn positive_rate(values: &[f64]) -> f64 {
    if values.is_empty() {
        0.0
    } else {
        values.iter().filter(|value| **value > 0.0).count() as f64 / values.len() as f64
    }
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
    let average = mean(values);
    let variance = values
        .iter()
        .map(|value| {
            let delta = *value - average;
            delta * delta
        })
        .sum::<f64>()
        / (values.len() as f64 - 1.0);
    variance.sqrt()
}

fn evidence_confidence(sample_count: usize, target: usize) -> f64 {
    if target == 0 {
        return 1.0;
    }
    ((sample_count as f64) / (target as f64))
        .sqrt()
        .clamp(0.0, 1.0)
}

fn shrink_to_neutral(score: f64, confidence: f64) -> f64 {
    bounded(50.0 + (score - 50.0) * confidence)
}

fn weighted_average(values: &[(f64, f64)]) -> f64 {
    let weight_sum = values.iter().map(|(_, weight)| *weight).sum::<f64>();
    if weight_sum <= EPSILON {
        0.0
    } else {
        values
            .iter()
            .map(|(value, weight)| value * weight)
            .sum::<f64>()
            / weight_sum
    }
}

fn percentile_map<T>(
    items: &[T],
    value_fn: impl Fn(&T) -> f64,
    higher_is_better: bool,
) -> HashMap<Uuid, f64>
where
    T: TraderScoped,
{
    let mut values = items
        .iter()
        .map(|item| (item.trader_id(), sanitize(value_fn(item))))
        .collect::<Vec<_>>();
    values.sort_by(|left, right| left.1.partial_cmp(&right.1).unwrap_or(Ordering::Equal));

    let mut scores = HashMap::new();
    if values.is_empty() {
        return scores;
    }
    if values.len() == 1 {
        scores.insert(values[0].0, 50.0);
        return scores;
    }

    let denominator = values.len() as f64 - 1.0;
    let mut index = 0usize;
    while index < values.len() {
        let mut end = index;
        while end + 1 < values.len() && (values[end + 1].1 - values[index].1).abs() <= EPSILON {
            end += 1;
        }
        let midpoint = (index + end) as f64 / 2.0;
        let percentile = (midpoint * 100.0) / denominator;
        let score = if higher_is_better {
            percentile
        } else {
            100.0 - percentile
        };
        for (trader_id, _) in &values[index..=end] {
            scores.insert(*trader_id, bounded(score));
        }
        index = end + 1;
    }

    scores
}

fn sanitize(value: f64) -> f64 {
    if value.is_finite() {
        value
    } else {
        0.0
    }
}

fn age_minutes(last_trade_at: Option<DateTime<Utc>>, as_of: DateTime<Utc>) -> f64 {
    last_trade_at
        .map(|timestamp| (as_of - timestamp).num_minutes().max(0) as f64)
        .unwrap_or(10_000.0)
}

fn score_of(scores: &HashMap<Uuid, f64>, trader_id: Uuid) -> f64 {
    scores.get(&trader_id).copied().unwrap_or(50.0)
}

fn bounded(value: f64) -> f64 {
    value.clamp(0.0, 100.0)
}

trait TraderScoped {
    fn trader_id(&self) -> Uuid;
}

impl TraderScoped for WalletQuantMetrics {
    fn trader_id(&self) -> Uuid {
        self.trader_id
    }
}

impl TraderScoped for TraderDiscoveryAggregate {
    fn trader_id(&self) -> Uuid {
        self.trader_id
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentile_map_preserves_order() {
        #[derive(Clone)]
        struct Point {
            trader_id: Uuid,
            value: f64,
        }
        impl TraderScoped for Point {
            fn trader_id(&self) -> Uuid {
                self.trader_id
            }
        }

        let low = Point {
            trader_id: Uuid::new_v4(),
            value: 1.0,
        };
        let mid = Point {
            trader_id: Uuid::new_v4(),
            value: 2.0,
        };
        let high = Point {
            trader_id: Uuid::new_v4(),
            value: 3.0,
        };
        let scores = percentile_map(
            &[low.clone(), mid.clone(), high.clone()],
            |item| item.value,
            true,
        );

        assert!(score_of(&scores, high.trader_id) > score_of(&scores, mid.trader_id));
        assert!(score_of(&scores, mid.trader_id) > score_of(&scores, low.trader_id));
    }

    #[test]
    fn shrink_to_neutral_moves_low_confidence_toward_fifty() {
        assert_eq!(shrink_to_neutral(80.0, 1.0), 80.0);
        assert_eq!(shrink_to_neutral(80.0, 0.0), 50.0);
        assert_eq!(shrink_to_neutral(20.0, 0.0), 50.0);
    }
}
