"""Metric catalog (documentation + future Polars column registry)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MetricGroup(str, Enum):
    performance = "performance"
    behavior = "behavior"
    market_context = "market_context"
    risk = "risk"


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """Describes a metric for incremental Polars implementations."""

    name: str
    group: MetricGroup
    description: str
    incremental: bool = True


PERFORMANCE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("realized_pnl", MetricGroup.performance, "Sum of realized PnL over window."),
    MetricSpec("unrealized_pnl", MetricGroup.performance, "Last mark-to-market unrealized."),
    MetricSpec("sharpe_ratio", MetricGroup.performance, "Mean/ vol of returns (configurable window)."),
    MetricSpec("sortino_ratio", MetricGroup.performance, "Downside deviation variant."),
    MetricSpec("win_rate", MetricGroup.performance, "Winning periods / trades ratio."),
    MetricSpec("expectancy", MetricGroup.performance, "Average PnL per trade."),
    MetricSpec("profit_factor", MetricGroup.performance, "Gross wins / gross losses."),
    MetricSpec("max_drawdown", MetricGroup.performance, "Peak-to-trough equity drawdown."),
)

BEHAVIOR_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("avg_hold_time_sec", MetricGroup.behavior, "Median hold duration."),
    MetricSpec("leverage_usage", MetricGroup.behavior, "Avg and max leverage."),
    MetricSpec("trade_frequency", MetricGroup.behavior, "Trades per day."),
    MetricSpec("adds_to_losers", MetricGroup.behavior, "Pyramiding into losing legs."),
    MetricSpec("adds_to_winners", MetricGroup.behavior, "Pyramiding into winners."),
    MetricSpec("directional_bias", MetricGroup.behavior, "Long vs short notional skew."),
    MetricSpec("volatility_preference", MetricGroup.behavior, "PnL vs realized vol regime."),
)

MARKET_CONTEXT_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("performance_by_asset", MetricGroup.market_context, "Per-coin attribution."),
    MetricSpec("performance_by_vol_regime", MetricGroup.market_context, "Bucketed by RV deciles."),
    MetricSpec("funding_sensitivity", MetricGroup.market_context, "PnL vs funding paid."),
    MetricSpec("liquidation_proximity", MetricGroup.market_context, "Distance to liq events."),
)

RISK_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("pnl_volatility", MetricGroup.risk, "Std dev of window returns."),
    MetricSpec("tail_risk", MetricGroup.risk, "CVaR-style tail loss proxy."),
    MetricSpec("max_consecutive_losses", MetricGroup.risk, "Streak statistic."),
    MetricSpec("position_concentration", MetricGroup.risk, "HHI on notionals."),
)

ALL_METRICS: tuple[MetricSpec, ...] = (
    PERFORMANCE_METRICS + BEHAVIOR_METRICS + MARKET_CONTEXT_METRICS + RISK_METRICS
)
