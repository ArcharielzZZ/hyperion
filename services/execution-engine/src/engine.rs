use crate::repository::{PersistedStrategyRun, PricePoint, Repository};
use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use hyperion_config::ExecutionEngineConfig;
use hyperion_models::{PaperTrade, Signal, SimulationBatch, SimulationReport, SimulationRun};
use hyperion_schemas::SimulationTriggerRequest;
use serde_json::json;
use std::{collections::HashMap, sync::Arc};
use tracing::info;
use uuid::Uuid;

#[derive(Debug, Clone, Copy)]
enum DirectionMode {
    Follow,
    Fade,
}

#[derive(Debug, Clone, Copy)]
struct ExitConfig {
    take_profit_bps: f64,
    stop_loss_bps: f64,
    max_hold_minutes: i64,
}

#[derive(Debug, Clone, Copy)]
struct RiskConfig {
    base_fraction: f64,
    max_fraction: f64,
    confidence_scale: f64,
}

#[derive(Debug, Clone, Copy)]
struct StrategyDefinition {
    key: &'static str,
    name: &'static str,
    direction_mode: DirectionMode,
    min_confidence: f64,
    min_momentum_score: f64,
    max_momentum_score: f64,
    allowed_regimes: &'static [&'static str],
    exit: ExitConfig,
    risk: RiskConfig,
}

#[derive(Debug, Clone, Default)]
struct MarketContext {
    volatility_1h_bps: f64,
    momentum_5m_bps: f64,
    momentum_15m_bps: f64,
    volatility_regime: String,
}

#[derive(Debug, Clone)]
struct ExitDecision {
    executed_exit_price: f64,
    raw_exit_price: f64,
    exit_slippage_bps: f64,
    exit_reason: &'static str,
    exit_timestamp: DateTime<Utc>,
    max_favorable_excursion_pct: f64,
    max_adverse_excursion_pct: f64,
}

pub async fn run_simulation(
    repository: Arc<Repository>,
    config: &ExecutionEngineConfig,
) -> Result<Option<SimulationBatch>> {
    run_simulation_with_request(
        repository,
        config,
        &SimulationTriggerRequest {
            replay_days: None,
            strategies: None,
        },
    )
    .await
}

pub async fn run_simulation_with_request(
    repository: Arc<Repository>,
    config: &ExecutionEngineConfig,
    request: &SimulationTriggerRequest,
) -> Result<Option<SimulationBatch>> {
    let replay_days = request.replay_days.unwrap_or(config.replay_days).max(1);
    let allowed_strategies = request.strategies.as_ref().map(|items| {
        items
            .iter()
            .map(|item| item.to_ascii_lowercase())
            .collect::<Vec<_>>()
    });
    let signals = repository.replay_signals(replay_days).await?;
    if signals.is_empty() {
        return Ok(None);
    }

    let dataset_start = signals
        .first()
        .map(|signal| signal.timestamp)
        .unwrap_or_else(Utc::now);
    let dataset_end = signals
        .last()
        .map(|signal| signal.timestamp)
        .unwrap_or_else(Utc::now);
    let batch = SimulationBatch {
        id: Uuid::new_v4(),
        name: format!(
            "historical_signal_replay_{}_{}_{}d",
            dataset_start.format("%Y%m%d%H%M"),
            dataset_end.format("%Y%m%d%H%M"),
            replay_days
        ),
        dataset_start,
        dataset_end,
        created_at: Utc::now(),
    };

    let mut runs = Vec::new();
    for strategy in strategies() {
        if !strategy_is_selected(strategy, allowed_strategies.as_deref()) {
            continue;
        }
        if let Some(run) =
            evaluate_strategy(repository.clone(), config, &batch, &signals, strategy).await?
        {
            runs.push(run);
        }
    }

    if runs.is_empty() {
        return Ok(None);
    }

    info!(
        batch_id = %batch.id,
        strategy_runs = runs.len(),
        dataset_start = %batch.dataset_start,
        dataset_end = %batch.dataset_end,
        "strategy evaluation batch completed"
    );
    repository.save_batch(&batch, &runs).await?;
    Ok(Some(batch))
}

async fn evaluate_strategy(
    repository: Arc<Repository>,
    config: &ExecutionEngineConfig,
    batch: &SimulationBatch,
    signals: &[Signal],
    strategy: StrategyDefinition,
) -> Result<Option<PersistedStrategyRun>> {
    let mut equity = config.starting_equity;
    let mut equity_curve = vec![(batch.dataset_start, equity)];
    let mut trades = Vec::new();

    for signal in signals {
        let context = parse_market_context(&signal.market_context);
        if !strategy_matches(strategy, signal) {
            continue;
        }

        let Some(entry_point) = repository
            .market_price_after(&signal.coin, signal.timestamp)
            .await?
        else {
            continue;
        };

        let desired_direction = strategy_direction(strategy, &signal.direction);
        let risk_fraction = position_risk_fraction(strategy, signal.confidence_score);
        let entry_slippage_bps =
            effective_slippage_bps(config.slippage_bps, risk_fraction, signal, &context);
        let executed_entry_price = apply_slippage(
            desired_direction,
            entry_point.price,
            entry_slippage_bps,
            true,
        );
        let quantity = ((equity * risk_fraction) / executed_entry_price.max(1.0)).max(0.0);
        if quantity <= 0.0 {
            continue;
        }

        let exit_deadline =
            entry_point.timestamp + Duration::minutes(strategy.exit.max_hold_minutes);
        let price_window = repository
            .market_window(&signal.coin, entry_point.timestamp, exit_deadline)
            .await?;
        if price_window.is_empty() {
            continue;
        }
        let exit_slippage_bps =
            effective_slippage_bps(config.slippage_bps, risk_fraction, signal, &context);
        let exit = evaluate_exit(
            desired_direction,
            executed_entry_price,
            &price_window,
            strategy.exit,
            exit_slippage_bps,
        );

        let entry_fee_paid = quantity * executed_entry_price * (config.fee_bps / 10_000.0);
        let exit_fee_paid = quantity * exit.executed_exit_price * (config.fee_bps / 10_000.0);
        let fee_paid = entry_fee_paid + exit_fee_paid;
        let gross_pnl = match desired_direction {
            "long" => (exit.executed_exit_price - executed_entry_price) * quantity,
            "short" => (executed_entry_price - exit.executed_exit_price) * quantity,
            _ => 0.0,
        };
        let pnl = gross_pnl - fee_paid;
        let equity_before = equity;
        equity += pnl;
        equity_curve.push((exit.exit_timestamp, equity));

        let raw_exit_delta = match desired_direction {
            "long" => exit.raw_exit_price - entry_point.price,
            "short" => entry_point.price - exit.raw_exit_price,
            _ => 0.0,
        };
        let slippage_paid = ((gross_pnl - (raw_exit_delta * quantity)).abs()).max(0.0);
        let return_pct = if equity_before > 0.0 {
            (pnl / equity_before) * 100.0
        } else {
            0.0
        };

        trades.push(PaperTrade {
            id: Uuid::new_v4(),
            run_id: Uuid::nil(),
            signal_id: Some(signal.id),
            strategy_key: strategy.key.to_string(),
            coin: signal.coin.clone(),
            direction: desired_direction.to_string(),
            entry_price: executed_entry_price,
            exit_price: exit.executed_exit_price,
            size: quantity,
            fee_paid,
            slippage_paid,
            pnl,
            entry_reason: "signal_replay".to_string(),
            exit_reason: exit.exit_reason.to_string(),
            regime: signal.regime.clone(),
            entry_fee_paid,
            exit_fee_paid,
            entry_slippage_bps,
            exit_slippage_bps: exit.exit_slippage_bps,
            return_pct,
            max_favorable_excursion_pct: exit.max_favorable_excursion_pct,
            max_adverse_excursion_pct: exit.max_adverse_excursion_pct,
            equity_before,
            equity_after: equity,
            risk_fraction,
            holding_minutes: ((exit.exit_timestamp - entry_point.timestamp).num_minutes()).max(0)
                as i32,
            confidence_score: signal.confidence_score,
            opened_at: entry_point.timestamp,
            closed_at: exit.exit_timestamp,
            created_at: Utc::now(),
        });
    }

    if trades.is_empty() {
        return Ok(None);
    }

    let run_id = Uuid::new_v4();
    for trade in &mut trades {
        trade.run_id = run_id;
    }

    let returns = trades
        .iter()
        .map(|trade| trade.return_pct)
        .collect::<Vec<_>>();
    let win_trades = trades
        .iter()
        .filter(|trade| trade.pnl > 0.0)
        .map(|trade| trade.return_pct)
        .collect::<Vec<_>>();
    let loss_trades = trades
        .iter()
        .filter(|trade| trade.pnl <= 0.0)
        .map(|trade| trade.return_pct)
        .collect::<Vec<_>>();
    let avg_win_pct = mean(&win_trades);
    let avg_loss_pct = mean(&loss_trades);
    let expectancy = expectancy(avg_win_pct, avg_loss_pct, win_trades.len(), trades.len());
    let profit_factor = profit_factor(&trades);
    let sharpe_like = sharpe_like(&returns);
    let avg_trade_return_pct = mean(&returns);
    let win_rate_pct = if trades.is_empty() {
        0.0
    } else {
        (win_trades.len() as f64 / trades.len() as f64) * 100.0
    };
    let (max_drawdown_pct, peak_equity, trough_equity, drawdown_curve_json) =
        drawdown_analytics(&equity_curve);
    let regime_breakdown_json = regime_breakdown_json(&trades);
    let (max_consecutive_wins, max_consecutive_losses) = streaks(&trades);
    let total_fees_paid = trades.iter().map(|trade| trade.fee_paid).sum::<f64>();
    let total_slippage_paid = trades.iter().map(|trade| trade.slippage_paid).sum::<f64>();

    let run = SimulationRun {
        id: run_id,
        batch_id: batch.id,
        strategy_key: strategy.key.to_string(),
        strategy_name: strategy.name.to_string(),
        dataset_start: batch.dataset_start,
        dataset_end: batch.dataset_end,
        starting_equity: config.starting_equity,
        ending_equity: equity,
        total_return_pct: ((equity - config.starting_equity) / config.starting_equity.max(1.0))
            * 100.0,
        max_drawdown_pct,
        win_rate_pct,
        trade_count: trades.len() as i32,
        sharpe_like,
        expectancy,
        profit_factor,
        avg_trade_return_pct,
        config_json: json!({
            "strategy_key": strategy.key,
            "exit": {
                "take_profit_bps": strategy.exit.take_profit_bps,
                "stop_loss_bps": strategy.exit.stop_loss_bps,
                "max_hold_minutes": strategy.exit.max_hold_minutes
            },
            "risk": {
                "base_fraction": strategy.risk.base_fraction,
                "max_fraction": strategy.risk.max_fraction,
                "confidence_scale": strategy.risk.confidence_scale
            },
            "fee_bps": config.fee_bps,
            "base_slippage_bps": config.slippage_bps,
            "starting_equity": config.starting_equity
        })
        .to_string(),
        created_at: Utc::now(),
    };

    let report = SimulationReport {
        id: Uuid::new_v4(),
        run_id,
        total_fees_paid,
        total_slippage_paid,
        avg_win_pct,
        avg_loss_pct,
        payoff_ratio: payoff_ratio(avg_win_pct, avg_loss_pct),
        return_volatility_pct: stddev(&returns),
        peak_equity,
        trough_equity,
        max_consecutive_wins,
        max_consecutive_losses,
        drawdown_curve_json,
        regime_breakdown_json,
        created_at: Utc::now(),
    };

    Ok(Some(PersistedStrategyRun {
        run,
        report,
        trades,
    }))
}

fn strategies() -> Vec<StrategyDefinition> {
    vec![
        StrategyDefinition {
            key: "consensus_momentum",
            name: "Consensus Momentum",
            direction_mode: DirectionMode::Follow,
            min_confidence: 60.0,
            min_momentum_score: 55.0,
            max_momentum_score: 100.0,
            allowed_regimes: &["trend_expansion", "balanced"],
            exit: ExitConfig {
                take_profit_bps: 140.0,
                stop_loss_bps: 85.0,
                max_hold_minutes: 240,
            },
            risk: RiskConfig {
                base_fraction: 0.015,
                max_fraction: 0.03,
                confidence_scale: 0.015,
            },
        },
        StrategyDefinition {
            key: "quality_breakout",
            name: "Quality Breakout",
            direction_mode: DirectionMode::Follow,
            min_confidence: 75.0,
            min_momentum_score: 65.0,
            max_momentum_score: 100.0,
            allowed_regimes: &["trend_expansion"],
            exit: ExitConfig {
                take_profit_bps: 200.0,
                stop_loss_bps: 90.0,
                max_hold_minutes: 360,
            },
            risk: RiskConfig {
                base_fraction: 0.02,
                max_fraction: 0.035,
                confidence_scale: 0.015,
            },
        },
        StrategyDefinition {
            key: "regime_fade",
            name: "Regime Fade",
            direction_mode: DirectionMode::Fade,
            min_confidence: 55.0,
            min_momentum_score: 0.0,
            max_momentum_score: 70.0,
            allowed_regimes: &["crowded", "volatile"],
            exit: ExitConfig {
                take_profit_bps: 90.0,
                stop_loss_bps: 70.0,
                max_hold_minutes: 120,
            },
            risk: RiskConfig {
                base_fraction: 0.01,
                max_fraction: 0.02,
                confidence_scale: 0.01,
            },
        },
    ]
}

fn strategy_is_selected(strategy: StrategyDefinition, allowed: Option<&[String]>) -> bool {
    allowed.is_none_or(|items| {
        items
            .iter()
            .any(|item| item == strategy.key || item == strategy.name.to_ascii_lowercase().as_str())
    })
}

fn strategy_matches(strategy: StrategyDefinition, signal: &Signal) -> bool {
    signal.confidence_score >= strategy.min_confidence
        && signal.momentum_score >= strategy.min_momentum_score
        && signal.momentum_score <= strategy.max_momentum_score
        && strategy
            .allowed_regimes
            .iter()
            .any(|regime| *regime == signal.regime)
}

fn strategy_direction(strategy: StrategyDefinition, signal_direction: &str) -> &'static str {
    match strategy.direction_mode {
        DirectionMode::Follow => {
            if signal_direction == "short" {
                "short"
            } else {
                "long"
            }
        }
        DirectionMode::Fade => {
            if signal_direction == "short" {
                "long"
            } else {
                "short"
            }
        }
    }
}

fn parse_market_context(raw: &str) -> MarketContext {
    let value = serde_json::from_str::<serde_json::Value>(raw).unwrap_or_default();
    MarketContext {
        volatility_1h_bps: value
            .get("volatility_1h_bps")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or_default(),
        momentum_5m_bps: value
            .get("momentum_5m_bps")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or_default(),
        momentum_15m_bps: value
            .get("momentum_15m_bps")
            .and_then(serde_json::Value::as_f64)
            .unwrap_or_default(),
        volatility_regime: value
            .get("volatility_regime")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("unknown")
            .to_string(),
    }
}

fn position_risk_fraction(strategy: StrategyDefinition, confidence_score: f64) -> f64 {
    let confidence_bonus =
        ((confidence_score / 100.0).clamp(0.0, 1.0)) * strategy.risk.confidence_scale;
    (strategy.risk.base_fraction + confidence_bonus)
        .clamp(strategy.risk.base_fraction, strategy.risk.max_fraction)
}

fn effective_slippage_bps(
    base_slippage_bps: f64,
    risk_fraction: f64,
    signal: &Signal,
    context: &MarketContext,
) -> f64 {
    let volatility_multiplier = 1.0 + (context.volatility_1h_bps / 80.0).clamp(0.0, 2.0);
    let momentum_multiplier = 1.0
        + ((context.momentum_5m_bps.abs() + context.momentum_15m_bps.abs()) / 100.0)
            .clamp(0.0, 1.0)
            * 0.15;
    let size_multiplier = 1.0 + (risk_fraction / 0.02).clamp(0.0, 2.0) * 0.25;
    let confidence_multiplier = 1.0 + (signal.confidence_score / 100.0) * 0.2;
    let regime_multiplier = match context.volatility_regime.as_str() {
        "high" => 1.2,
        "extreme" => 1.5,
        _ => 1.0,
    };
    base_slippage_bps
        * volatility_multiplier
        * momentum_multiplier
        * size_multiplier
        * confidence_multiplier
        * regime_multiplier
}

fn apply_slippage(direction: &str, price: f64, slippage_bps: f64, is_entry: bool) -> f64 {
    let multiplier = slippage_bps / 10_000.0;
    match (direction, is_entry) {
        ("long", true) => price * (1.0 + multiplier),
        ("long", false) => price * (1.0 - multiplier),
        ("short", true) => price * (1.0 - multiplier),
        ("short", false) => price * (1.0 + multiplier),
        _ => price,
    }
}

fn evaluate_exit(
    direction: &str,
    executed_entry_price: f64,
    prices: &[PricePoint],
    exit_config: ExitConfig,
    effective_exit_slippage_bps: f64,
) -> ExitDecision {
    let mut chosen = prices.last().cloned().unwrap_or(PricePoint {
        price: executed_entry_price,
        timestamp: Utc::now(),
    });
    let mut exit_reason = "time_exit";

    for point in prices {
        let move_bps = directional_return_pct(direction, executed_entry_price, point.price) * 100.0;
        if move_bps >= exit_config.take_profit_bps {
            chosen = point.clone();
            exit_reason = "take_profit";
            break;
        }
        if move_bps <= -exit_config.stop_loss_bps {
            chosen = point.clone();
            exit_reason = "stop_loss";
            break;
        }
    }

    let max_favorable_excursion_pct = prices
        .iter()
        .map(|point| directional_return_pct(direction, executed_entry_price, point.price))
        .fold(f64::MIN, f64::max)
        .max(0.0);
    let max_adverse_excursion_pct = prices
        .iter()
        .map(|point| directional_return_pct(direction, executed_entry_price, point.price))
        .fold(f64::MAX, f64::min)
        .min(0.0);

    ExitDecision {
        executed_exit_price: apply_slippage(
            direction,
            chosen.price,
            effective_exit_slippage_bps,
            false,
        ),
        raw_exit_price: chosen.price,
        exit_slippage_bps: effective_exit_slippage_bps,
        exit_reason,
        exit_timestamp: chosen.timestamp,
        max_favorable_excursion_pct,
        max_adverse_excursion_pct,
    }
}

fn directional_return_pct(direction: &str, entry_price: f64, current_price: f64) -> f64 {
    if entry_price <= 0.0 || current_price <= 0.0 {
        return 0.0;
    }
    match direction {
        "long" => ((current_price / entry_price) - 1.0) * 100.0,
        "short" => ((entry_price / current_price) - 1.0) * 100.0,
        _ => 0.0,
    }
}

fn drawdown_analytics(curve: &[(DateTime<Utc>, f64)]) -> (f64, f64, f64, String) {
    let mut peak = curve.first().map(|(_, equity)| *equity).unwrap_or(0.0);
    let mut max_drawdown: f64 = 0.0;
    let mut trough = peak;
    let points = curve
        .iter()
        .map(|(timestamp, equity)| {
            peak = peak.max(*equity);
            trough = trough.min(*equity);
            let drawdown_pct = if peak > 0.0 {
                ((peak - *equity) / peak) * 100.0
            } else {
                0.0
            };
            max_drawdown = max_drawdown.max(drawdown_pct);
            json!({
                "timestamp": timestamp,
                "equity": equity,
                "drawdown_pct": drawdown_pct
            })
        })
        .collect::<Vec<_>>();
    (
        max_drawdown,
        peak,
        trough,
        serde_json::Value::Array(points).to_string(),
    )
}

fn regime_breakdown_json(trades: &[PaperTrade]) -> String {
    let mut groups = HashMap::<String, Vec<&PaperTrade>>::new();
    for trade in trades {
        groups.entry(trade.regime.clone()).or_default().push(trade);
    }

    let payload = groups
        .into_iter()
        .map(|(regime, trades)| {
            let returns = trades.iter().map(|trade| trade.return_pct).collect::<Vec<_>>();
            let wins = trades.iter().filter(|trade| trade.pnl > 0.0).count();
            json!({
                "regime": regime,
                "trades": trades.len(),
                "win_rate_pct": if trades.is_empty() { 0.0 } else { (wins as f64 / trades.len() as f64) * 100.0 },
                "avg_return_pct": mean(&returns),
                "total_pnl": trades.iter().map(|trade| trade.pnl).sum::<f64>()
            })
        })
        .collect::<Vec<_>>();

    serde_json::Value::Array(payload).to_string()
}

fn streaks(trades: &[PaperTrade]) -> (i32, i32) {
    let mut wins = 0;
    let mut losses = 0;
    let mut max_wins = 0;
    let mut max_losses = 0;

    for trade in trades {
        if trade.pnl > 0.0 {
            wins += 1;
            losses = 0;
        } else {
            losses += 1;
            wins = 0;
        }
        max_wins = max_wins.max(wins);
        max_losses = max_losses.max(losses);
    }

    (max_wins, max_losses)
}

fn sharpe_like(returns: &[f64]) -> f64 {
    let volatility = stddev(returns);
    if volatility <= 0.0 {
        return 0.0;
    }
    mean(returns) / volatility * (returns.len() as f64).sqrt()
}

fn expectancy(avg_win_pct: f64, avg_loss_pct: f64, wins: usize, total: usize) -> f64 {
    if total == 0 {
        return 0.0;
    }
    let win_rate = wins as f64 / total as f64;
    let loss_rate = 1.0 - win_rate;
    (avg_win_pct * win_rate) + (avg_loss_pct * loss_rate)
}

fn profit_factor(trades: &[PaperTrade]) -> f64 {
    let gross_wins = trades
        .iter()
        .filter(|trade| trade.pnl > 0.0)
        .map(|trade| trade.pnl)
        .sum::<f64>();
    let gross_losses = trades
        .iter()
        .filter(|trade| trade.pnl < 0.0)
        .map(|trade| trade.pnl.abs())
        .sum::<f64>();
    if gross_losses <= 0.0 {
        gross_wins
    } else {
        gross_wins / gross_losses
    }
}

fn payoff_ratio(avg_win_pct: f64, avg_loss_pct: f64) -> f64 {
    if avg_loss_pct >= 0.0 {
        avg_win_pct
    } else {
        avg_win_pct / avg_loss_pct.abs().max(0.0001)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sharpe_like_rewards_better_return_series() {
        let stable = vec![0.8, 1.0, 0.9, 1.1];
        let noisy = vec![0.8, -1.0, 1.5, -0.4];
        assert!(sharpe_like(&stable) > sharpe_like(&noisy));
    }

    #[test]
    fn fade_strategy_flips_direction() {
        let direction = strategy_direction(strategies()[2], "long");
        assert_eq!(direction, "short");
    }

    #[test]
    fn slippage_is_adverse_for_long_entries() {
        let adjusted = apply_slippage("long", 100.0, 10.0, true);
        assert!(adjusted > 100.0);
    }
}
