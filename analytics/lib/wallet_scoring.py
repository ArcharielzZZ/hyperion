"""Offline wallet scoring from bundle Parquet fills (Research preview).

Ports metric logic and style classification from ``services/trader-engine/src/scoring.rs``.
Uses absolute (single-wallet) scoring instead of cohort percentiles.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import polars as pl

EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class BundleWalletMetrics:
    active_days: int
    fills_count: int
    daily_return_count: int
    leverage_count: int
    entry_edge_count: int
    sharpe_like: float
    return_vol_bps: float
    downside_vol_bps: float
    hit_rate_pct: float
    max_drawdown_pct: float
    recovery_factor: float
    entry_timing_edge_bps: float
    favorable_entry_rate_pct: float
    avg_leverage: float
    max_leverage: float
    leverage_volatility: float
    sizing_cv: float
    expectancy_bps: float
    profit_factor: float
    avg_holding_hours: float
    leverage_neutral: bool = True


@dataclass(frozen=True, slots=True)
class BundleScoreResult:
    metrics: BundleWalletMetrics
    consistency_score: float
    survivability_score: float
    timing_score: float
    leverage_discipline_score: float
    conviction_score: float
    total_score: float
    style: str
    style_confidence: float
    skip_reason: str | None = None


def _sanitize(value: float) -> float:
    if math.isfinite(value):
        return value
    return 0.0


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    var = sum((v - avg) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(var)


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, value))


def _sigmoid_norm(value: float, midpoint: float, steepness: float) -> float:
    value = _sanitize(value)
    midpoint = _sanitize(midpoint)
    steepness = _sanitize(steepness)
    return max(0.0, min(1.0, 1.0 / (1.0 + math.exp(-steepness * (value - midpoint)))))


def _bell_norm(value: float, center: float, width: float) -> float:
    if width <= EPSILON:
        return 0.0
    delta = (_sanitize(value) - _sanitize(center)) / width
    return max(0.0, min(1.0, math.exp(-0.5 * delta * delta)))


def _weighted_average(values: list[tuple[float, float]]) -> float:
    weight_sum = sum(w for _, w in values)
    if weight_sum <= EPSILON:
        return 0.0
    return sum(v * w for v, w in values) / weight_sum


def _weighted_fit(values: list[tuple[float, float]]) -> float:
    return max(0.0, min(1.0, _weighted_average(values)))


def _evidence_confidence(sample_count: int, target: int) -> float:
    if target == 0:
        return 1.0
    return max(0.0, min(1.0, math.sqrt(sample_count / target)))


def _shrink_to_neutral(score: float, confidence: float) -> float:
    return _bounded(50.0 + (score - 50.0) * confidence)


def _positive_rate(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if v > 0.0) / len(values)


def _downside_stddev(values: list[float]) -> float:
    negatives = [v for v in values if v < 0.0]
    return _stddev(negatives)


def _sharpe_like(values: list[float]) -> float:
    vol = _stddev(values)
    if vol <= EPSILON:
        return 0.0
    return _mean(values) / vol


def _coefficient_of_variation(values: list[float]) -> float:
    avg = abs(_mean(values))
    if avg <= EPSILON:
        return 0.0
    return _stddev(values) / avg


def _profit_factor(returns_bps: list[float]) -> float:
    gains = sum(v for v in returns_bps if v > 0.0)
    losses = abs(sum(v for v in returns_bps if v < 0.0))
    if losses <= EPSILON:
        return 5.0 if gains > EPSILON else 1.0
    return gains / losses


def _max_drawdown_pct(curve: list[tuple[int, float]]) -> float:
    peak = 0.0
    drawdown = 0.0
    for _, equity in curve:
        peak = max(peak, equity)
        if peak > EPSILON:
            drawdown = max(drawdown, ((peak - equity) / peak) * 100.0)
    return drawdown


def _recovery_factor(curve: list[tuple[int, float]], max_dd_pct: float) -> float:
    if len(curve) < 2:
        return 0.0
    start = curve[0][1]
    end = curve[-1][1]
    if abs(start) <= EPSILON:
        return 0.0
    total_return_pct = ((end / start) - 1.0) * 100.0
    return total_return_pct / max(max_dd_pct, 1.0)


def _equity_returns_bps(curve: list[tuple[int, float]]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(curve)):
        prev = curve[i - 1][1]
        cur = curve[i][1]
        if abs(prev) > EPSILON:
            out.append(((cur / prev) - 1.0) * 10_000.0)
    return out


def _entry_edge_bps(side: str, fill_price: float, future_price: float | None) -> float | None:
    if future_price is None:
        return None
    denom = max(abs(fill_price), 1.0)
    side_l = side.lower()
    if side_l == "buy":
        return ((future_price / denom) - 1.0) * 10_000.0
    if side_l == "sell":
        return ((denom / max(abs(future_price), 1.0)) - 1.0) * 10_000.0
    return None


def _parse_dir(fill_dir: str | None) -> tuple[bool | None, bool | None]:
    if not fill_dir:
        return None, None
    lu = fill_dir.strip().lower()
    if lu.startswith("close"):
        return False, True
    if lu.startswith("open"):
        return True, False
    return None, None


def _fifo_holding_hours(fills: pl.DataFrame) -> list[float]:
    """Return closed round-trip hold durations in hours from ``dir`` labels."""

    if fills.is_empty() or "dir" not in fills.columns:
        return []

    long_lots: dict[str, deque[tuple[datetime, float]]] = defaultdict(deque)
    short_lots: dict[str, deque[tuple[datetime, float]]] = defaultdict(deque)
    durations: list[float] = []

    rows = fills.sort("timestamp").select("timestamp", "coin", "dir", "size").iter_rows(named=True)
    for row in rows:
        ts = row["timestamp"]
        if isinstance(ts, datetime) and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        coin = str(row["coin"])
        size = abs(float(row["size"] or 0.0))
        if size <= EPSILON:
            continue
        is_open, is_close = _parse_dir(row.get("dir"))
        dir_l = str(row.get("dir", "")).lower()
        if is_open:
            if "long" in dir_l:
                long_lots[coin].append((ts, size))
            elif "short" in dir_l:
                short_lots[coin].append((ts, size))
        elif is_close:
            if "long" not in dir_l and "short" not in dir_l:
                continue
            lots = long_lots[coin] if "long" in dir_l else short_lots[coin]
            remaining = size
            while remaining > EPSILON and lots:
                open_ts, open_qty = lots[0]
                matched = min(remaining, open_qty)
                hold_h = max((ts - open_ts).total_seconds() / 3600.0, 0.0)
                durations.append(hold_h)
                remaining -= matched
                if open_qty - matched <= EPSILON:
                    lots.popleft()
                else:
                    lots[0] = (open_ts, open_qty - matched)

    return durations


def _daily_pnl_curve(fills: pl.DataFrame) -> tuple[list[tuple[int, float]], dict[date, float]]:
    if fills.is_empty():
        return [], {}

    daily = (
        fills.with_columns(pl.col("timestamp").dt.date().alias("day"))
        .group_by("day")
        .agg(pl.col("closed_pnl").sum().alias("daily_pnl"))
        .sort("day")
    )
    by_day: dict[date, float] = {}
    cumulative = 0.0
    curve: list[tuple[int, float]] = []
    for i, row in enumerate(daily.iter_rows(named=True)):
        pnl = float(row["daily_pnl"] or 0.0)
        d = row["day"]
        if isinstance(d, datetime):
            d = d.date()
        by_day[d] = pnl
        cumulative += pnl
        base = 10_000.0 + cumulative
        curve.append((i, max(base, 1.0)))

    return curve, by_day


def _active_days(fills: pl.DataFrame) -> int:
    if fills.is_empty():
        return 0
    days = fills.select(pl.col("timestamp").dt.date().alias("day")).unique()
    return len(days)


def _compute_entry_edges(
    fills: pl.DataFrame,
    candles_by_coin: dict[str, pl.DataFrame] | None,
) -> list[float]:
    if candles_by_coin is None or fills.is_empty():
        return []

    edges: list[float] = []
    base = fills.select("timestamp", "coin", "side", "price")
    for coin, candles in candles_by_coin.items():
        if candles.is_empty():
            continue
        coin_fills = base.filter(pl.col("coin") == coin).sort("timestamp")
        if coin_fills.is_empty():
            continue
        cndl = candles.select(
            pl.col("timestamp").alias("candle_ts"),
            pl.col("close").alias("future_close"),
        ).sort("candle_ts")
        joined = coin_fills.join_asof(
            cndl,
            left_on="timestamp",
            right_on="candle_ts",
            strategy="forward",
        )
        for row in joined.iter_rows(named=True):
            edge = _entry_edge_bps(
                str(row["side"]),
                float(row["price"]),
                None if row["future_close"] is None else float(row["future_close"]),
            )
            if edge is not None:
                edges.append(edge)
    return edges


def _metric_score(
    value: float,
    midpoint: float,
    steepness: float,
    *,
    higher_is_better: bool = True,
) -> float:
    norm = _sigmoid_norm(value, midpoint, steepness)
    if not higher_is_better:
        norm = 1.0 - norm
    return _bounded(norm * 100.0)


def compute_bundle_metrics(
    fills: pl.DataFrame,
    candles_by_coin: dict[str, pl.DataFrame] | None = None,
) -> BundleWalletMetrics | None:
    if fills.is_empty():
        return None

    active_days = _active_days(fills)
    if active_days == 0:
        return None

    curve, _ = _daily_pnl_curve(fills)
    if len(curve) < 1:
        return None

    daily_returns = _equity_returns_bps(curve)
    max_dd = _max_drawdown_pct(curve)
    recovery = _recovery_factor(curve, max_dd)

    notionals = [
        abs(float(r["size"]) * float(r["price"]))
        for r in fills.select("size", "price").iter_rows(named=True)
        if abs(float(r["size"]) * float(r["price"])) > 0.0
    ]
    holds = _fifo_holding_hours(fills)
    entry_edges = _compute_entry_edges(fills, candles_by_coin)

    return BundleWalletMetrics(
        active_days=active_days,
        fills_count=len(fills),
        daily_return_count=len(daily_returns),
        leverage_count=0,
        entry_edge_count=len(entry_edges),
        sharpe_like=_sharpe_like(daily_returns),
        return_vol_bps=_stddev(daily_returns),
        downside_vol_bps=_downside_stddev(daily_returns),
        hit_rate_pct=_positive_rate(daily_returns) * 100.0,
        max_drawdown_pct=max_dd,
        recovery_factor=recovery,
        entry_timing_edge_bps=_mean(entry_edges),
        favorable_entry_rate_pct=_positive_rate(entry_edges) * 100.0,
        avg_leverage=0.0,
        max_leverage=0.0,
        leverage_volatility=0.0,
        sizing_cv=_coefficient_of_variation(notionals),
        expectancy_bps=_mean(daily_returns),
        profit_factor=_profit_factor(daily_returns),
        avg_holding_hours=_mean(holds),
        leverage_neutral=True,
    )


def _scalp_fit(item: BundleWalletMetrics) -> float:
    holding_fit = 1.0 - _sigmoid_norm(item.avg_holding_hours, 4.0, 0.5)
    fills_fit = _sigmoid_norm(float(item.fills_count), 50.0, 0.04)
    leverage_fit = _bell_norm(item.avg_leverage, 5.0, 3.0)
    sizing_fit = 1.0 - _sigmoid_norm(item.sizing_cv, 0.8, 3.0)
    return _weighted_fit([
        (holding_fit, 0.45),
        (fills_fit, 0.30),
        (leverage_fit, 0.15),
        (sizing_fit, 0.10),
    ])


def _momentum_fit(item: BundleWalletMetrics) -> float:
    if item.entry_timing_edge_bps <= 0.0 or item.expectancy_bps <= 0.0:
        return 0.0
    edge_fit = _sigmoid_norm(item.entry_timing_edge_bps, 10.0, 0.15)
    expectancy_fit = _sigmoid_norm(item.expectancy_bps, 5.0, 0.2)
    favorable_fit = _sigmoid_norm(item.favorable_entry_rate_pct, 55.0, 0.12)
    holding_fit = _bell_norm(item.avg_holding_hours, 12.0, 20.0)
    return _weighted_fit([
        (edge_fit, 0.40),
        (expectancy_fit, 0.30),
        (favorable_fit, 0.20),
        (holding_fit, 0.10),
    ])


def _mean_reversion_fit(item: BundleWalletMetrics) -> float:
    if item.entry_timing_edge_bps >= 0.0 or item.expectancy_bps <= 0.0:
        return 0.0
    edge_fit = _sigmoid_norm(-item.entry_timing_edge_bps, 5.0, 0.15)
    expectancy_fit = _sigmoid_norm(item.expectancy_bps, 3.0, 0.25)
    profit_factor_fit = _sigmoid_norm(item.profit_factor - 1.0, 0.5, 1.5)
    recovery_fit = _sigmoid_norm(item.recovery_factor, 1.0, 0.8)
    return _weighted_fit([
        (edge_fit, 0.40),
        (expectancy_fit, 0.25),
        (profit_factor_fit, 0.20),
        (recovery_fit, 0.15),
    ])


def _swing_fit(item: BundleWalletMetrics) -> float:
    holding_fit = _sigmoid_norm(item.avg_holding_hours, 24.0, 0.06)
    fills_fit = 1.0 - _sigmoid_norm(float(item.fills_count), 20.0, 0.08)
    drawdown_fit = 1.0 - _sigmoid_norm(item.max_drawdown_pct, 15.0, 0.1)
    leverage_fit = 1.0 - _sigmoid_norm(item.leverage_volatility, 3.0, 0.4)
    return _weighted_fit([
        (holding_fit, 0.45),
        (fills_fit, 0.25),
        (drawdown_fit, 0.20),
        (leverage_fit, 0.10),
    ])


def _style_fit_scores(item: BundleWalletMetrics) -> list[tuple[str, float]]:
    return [
        ("scalp", _scalp_fit(item)),
        ("momentum", _momentum_fit(item)),
        ("mean_reversion", _mean_reversion_fit(item)),
        ("swing", _swing_fit(item)),
    ]


def classify_style(item: BundleWalletMetrics) -> tuple[str, float]:
    min_style_fit = 0.35
    min_non_swing_margin = 0.05

    scores = sorted(_style_fit_scores(item), key=lambda x: x[1], reverse=True)
    best_style, best_score = scores[0]
    runner_up_score = scores[1][1]

    if best_score < min_style_fit:
        return "unknown", best_score

    margin = max(0.0, min(1.0, best_score - runner_up_score))
    if best_style != "swing" and margin < min_non_swing_margin:
        return "unknown", margin

    return best_style, margin


def score_bundle_wallet(metrics: BundleWalletMetrics) -> BundleScoreResult:
    consistency_confidence = _evidence_confidence(metrics.daily_return_count, 20)
    survivability_confidence = min(
        _evidence_confidence(metrics.active_days, 10),
        _evidence_confidence(metrics.daily_return_count, 20),
    )
    timing_confidence = _evidence_confidence(metrics.entry_edge_count, 15)
    leverage_confidence = 0.0 if metrics.leverage_neutral else _evidence_confidence(metrics.leverage_count, 20)
    conviction_confidence = min(
        _evidence_confidence(metrics.daily_return_count, 20),
        _evidence_confidence(metrics.fills_count, 20),
    )

    sharpe_s = _metric_score(metrics.sharpe_like, 1.0, 1.0)
    vol_s = _metric_score(metrics.return_vol_bps, 80.0, 0.03, higher_is_better=False)
    down_s = _metric_score(metrics.downside_vol_bps, 60.0, 0.04, higher_is_better=False)
    hit_s = _bounded(metrics.hit_rate_pct)

    consistency_score = _shrink_to_neutral(
        _weighted_average([
            (sharpe_s, 0.40),
            (vol_s, 0.20),
            (down_s, 0.20),
            (hit_s, 0.20),
        ]),
        consistency_confidence,
    )

    dd_s = _metric_score(metrics.max_drawdown_pct, 20.0, 0.08, higher_is_better=False)
    rec_s = _metric_score(metrics.recovery_factor, 2.0, 0.5)
    active_s = _metric_score(float(metrics.active_days), 15.0, 0.15)
    survivability_score = _shrink_to_neutral(
        _weighted_average([(dd_s, 0.50), (rec_s, 0.30), (active_s, 0.20)]),
        survivability_confidence,
    )

    edge_s = _metric_score(metrics.entry_timing_edge_bps, 5.0, 0.12)
    fav_s = _bounded(metrics.favorable_entry_rate_pct)
    timing_score = _shrink_to_neutral(
        _weighted_average([(edge_s, 0.65), (fav_s, 0.35)]),
        timing_confidence,
    )

    if metrics.leverage_neutral:
        leverage_discipline_score = 50.0
    else:
        max_lev_s = _metric_score(metrics.max_leverage, 8.0, 0.4, higher_is_better=False)
        avg_lev_s = _metric_score(metrics.avg_leverage, 6.0, 0.35, higher_is_better=False)
        lev_vol_s = _metric_score(metrics.leverage_volatility, 3.0, 0.4, higher_is_better=False)
        size_s = _metric_score(metrics.sizing_cv, 0.8, 2.0, higher_is_better=False)
        leverage_discipline_score = _shrink_to_neutral(
            _weighted_average([
                (max_lev_s, 0.40),
                (avg_lev_s, 0.35),
                (lev_vol_s, 0.15),
                (size_s, 0.10),
            ]),
            leverage_confidence,
        )

    exp_s = _metric_score(metrics.expectancy_bps, 3.0, 0.2)
    pf_s = _metric_score(metrics.profit_factor, 1.5, 1.0)
    fills_s = _metric_score(float(metrics.fills_count), 100.0, 0.02)
    conviction_score = _shrink_to_neutral(
        _weighted_average([
            (exp_s, 0.40),
            (pf_s, 0.35),
            (active_s, 0.15),
            (fills_s, 0.10),
        ]),
        conviction_confidence,
    )

    total_score = _bounded(_weighted_average([
        (consistency_score, 0.30),
        (survivability_score, 0.25),
        (timing_score, 0.15),
        (leverage_discipline_score, 0.15),
        (conviction_score, 0.15),
    ]))

    style, style_conf = classify_style(metrics)

    return BundleScoreResult(
        metrics=metrics,
        consistency_score=consistency_score,
        survivability_score=survivability_score,
        timing_score=timing_score,
        leverage_discipline_score=leverage_discipline_score,
        conviction_score=conviction_score,
        total_score=total_score,
        style=style,
        style_confidence=style_conf,
    )


def _load_candles_by_coin(wallet: str, fills: pl.DataFrame) -> dict[str, pl.DataFrame]:
    from analytics.dashboard import wallet_pulls

    out: dict[str, pl.DataFrame] = {}
    for coin in fills["coin"].unique().to_list():
        candles = wallet_pulls.load_candles(wallet, str(coin), wallet_pulls.DEFAULT_INTERVAL)
        if candles is not None and not candles.is_empty():
            out[str(coin)] = candles
    return out


def score_wallet_from_fills(
    fills: pl.DataFrame,
    *,
    candles_by_coin: dict[str, pl.DataFrame] | None = None,
) -> BundleScoreResult | None:
    metrics = compute_bundle_metrics(fills, candles_by_coin)
    if metrics is None:
        return None
    if metrics.active_days < 2:
        return BundleScoreResult(
            metrics=metrics,
            consistency_score=50.0,
            survivability_score=50.0,
            timing_score=50.0,
            leverage_discipline_score=50.0,
            conviction_score=50.0,
            total_score=50.0,
            style="unknown",
            style_confidence=0.0,
            skip_reason="Zu wenig aktive Tage fuer belastbaren Score (min. 2).",
        )
    return score_bundle_wallet(metrics)


def score_wallet_from_parquet(wallet: str) -> BundleScoreResult | None:
    from analytics.dashboard import wallet_pulls

    fills = wallet_pulls.load_fills(wallet.lower())
    candles = _load_candles_by_coin(wallet.lower(), fills)
    return score_wallet_from_fills(fills, candles_by_coin=candles or None)


def format_ts(value: Any) -> str:
    if value is None:
        return "?"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value)[:19]
