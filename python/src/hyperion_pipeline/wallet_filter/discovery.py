"""Discovery score for PROMOTED wallets only (SUMMARY_OPTIMIERT §04)."""

from __future__ import annotations

from datetime import datetime, timezone

from hyperion_pipeline.wallet_filter.config import WalletFilterConfig


def _linear_score(value: float, *, good: float, bad: float, higher_is_better: bool) -> float:
    if good == bad:
        return 50.0
    if higher_is_better:
        if value >= good:
            return 100.0
        if value <= bad:
            return 0.0
        return 100.0 * (value - bad) / (good - bad)
    if value <= good:
        return 100.0
    if value >= bad:
        return 0.0
    return 100.0 * (bad - value) / (bad - good)


def discovery_score_promoted(
    *,
    data_completeness_pct: float,
    pnl_snapshots_30d: int,
    fills_30d: int,
    has_behavior_score: bool,
    last_trade_at: datetime | None,
    trade_count_24h: int,
    active_days_7d: int,
    scoring_runs: int,
    history_stability: float,
    cfg: WalletFilterConfig,
    as_of: datetime | None = None,
) -> float:
    anchor = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)

    completeness_pts = _linear_score(data_completeness_pct, good=90.0, bad=40.0, higher_is_better=True)
    snap_pts = _linear_score(float(pnl_snapshots_30d), good=30.0, bad=5.0, higher_is_better=True)
    fills_pts = 100.0 if fills_30d > 0 else 0.0
    behavior_pts = 100.0 if has_behavior_score else 0.0
    coverage = 0.40 * completeness_pts + 0.30 * snap_pts + 0.15 * fills_pts + 0.15 * behavior_pts

    if last_trade_at is None:
        age_pts = 0.0
    else:
        age_h = (anchor - last_trade_at.astimezone(timezone.utc)).total_seconds() / 3600.0
        age_pts = _linear_score(age_h, good=6.0, bad=168.0, higher_is_better=False)
    trade_pts = _linear_score(float(trade_count_24h), good=20.0, bad=0.0, higher_is_better=True)
    consist_pts = _linear_score(float(active_days_7d), good=5.0, bad=0.0, higher_is_better=True)
    activity = 0.40 * age_pts + 0.35 * trade_pts + 0.25 * consist_pts

    if scoring_runs < cfg.discovery_cold_start_runs:
        return min(100.0, 0.65 * coverage + 0.35 * activity)

    days_seen_pts = _linear_score(float(scoring_runs), good=20.0, bad=2.0, higher_is_better=True)
    stability_pts = _linear_score(history_stability, good=0.85, bad=0.40, higher_is_better=True)
    history = 0.35 * days_seen_pts + 0.35 * coverage + 0.30 * stability_pts
    return min(100.0, 0.50 * coverage + 0.30 * activity + 0.20 * history)
