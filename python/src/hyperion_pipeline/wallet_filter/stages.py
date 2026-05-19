"""Stages 0–3 from wallet_filter_system/SUMMARY_OPTIMIERT."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.wallet_filter.aggregates import multi_window_from_fills
from hyperion_pipeline.wallet_filter.config import WalletFilterConfig
from hyperion_pipeline.wallet_filter.models import MultiWindowPnL, WalletLabel, WalletStatus


def stage0_route(
    *,
    existing_status: WalletStatus | None,
    force_whale_promoted: bool,
) -> tuple[str, bool]:
    """
    Returns (action, skip_pipeline).
    action: process | block | promote_fast_path
    """

    if force_whale_promoted:
        return "promote_fast_path", False
    if existing_status == "BANNED":
        return "block", True
    if existing_status == "PROMOTED":
        return "promote_fast_path", False
    return "process", False


def stage1_hf_label(
    fills: list[TradeFill],
    cfg: WalletFilterConfig,
) -> WalletLabel:
    """Classify HF bot vs normal/new from timestamp density only."""

    if not fills:
        return "NEW_WALLET"

    ordered = sorted(fills, key=lambda f: f.event_timestamp)[-cfg.max_fills_sample :]
    n = len(ordered)
    oldest = ordered[0].event_timestamp.astimezone(timezone.utc)
    newest = ordered[-1].event_timestamp.astimezone(timezone.utc)
    span_days = max((newest - oldest).total_seconds() / 86_400.0, 0.0)

    if n >= cfg.hf_fill_cap:
        if span_days < cfg.ultra_hf_max_span_days:
            return "ULTRA_HF_BOT"
        if span_days < cfg.high_freq_max_span_days:
            return "HIGH_FREQUENCY"
        return "NORMAL"

    if span_days >= cfg.normal_min_history_days:
        return "NORMAL"
    return "NEW_WALLET"


def label_to_status(label: WalletLabel) -> WalletStatus:
    if label in ("ULTRA_HF_BOT", "HIGH_FREQUENCY"):
        return "BANNED"
    return "PENDING"


def stage2_promotion(
    mw: MultiWindowPnL,
    cfg: WalletFilterConfig,
    *,
    as_of: datetime | None = None,
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    anchor = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if mw.last_trade_at is None:
        notes.append("no_trades")
        return False, notes

    inactive_days = (anchor - mw.last_trade_at).total_seconds() / 86_400.0
    if inactive_days > cfg.max_inactive_days:
        notes.append(f"inactive_{inactive_days:.0f}d")
        return False, notes

    if mw.total_trades < cfg.min_trades:
        notes.append(f"trades_{mw.total_trades}<{cfg.min_trades}")
        return False, notes

    if mw.total_volume < cfg.min_volume_usd:
        notes.append(f"volume_{mw.total_volume:.0f}<{cfg.min_volume_usd}")
        return False, notes

    pnl_positive_window = mw.pnl_3m > 0 or mw.pnl_1m > 0 or mw.pnl_7d > 0
    if not pnl_positive_window:
        notes.append("no_positive_pnl_window")
        return False, notes

    sample_pnl = mw.pnl_3m if mw.pnl_3m != 0 else (mw.pnl_1m if mw.pnl_1m != 0 else mw.pnl_7d)
    min_pnl = cfg.pnl_efficiency_ratio * mw.total_volume
    if sample_pnl <= min_pnl:
        notes.append(f"pnl_efficiency {sample_pnl:.2f}<={min_pnl:.2f}")
        return False, notes

    notes.append("stage2_pass")
    return True, notes


def stage3_deep_dive_eligible(
    *,
    pnl_snapshots_30d: int,
    fills_30d: int,
    data_completeness_pct: float,
    avg_confidence: float,
    cfg: WalletFilterConfig,
) -> tuple[bool, list[str]]:
    notes: list[str] = []
    if pnl_snapshots_30d < cfg.deep_min_pnl_snapshots_30d:
        notes.append(f"snapshots_{pnl_snapshots_30d}<{cfg.deep_min_pnl_snapshots_30d}")
        return False, notes
    if data_completeness_pct < cfg.deep_min_data_completeness_pct:
        notes.append(f"completeness_{data_completeness_pct:.0f}<{cfg.deep_min_data_completeness_pct}")
        return False, notes
    if avg_confidence < cfg.deep_min_avg_confidence:
        notes.append(f"confidence_{avg_confidence:.2f}<{cfg.deep_min_avg_confidence}")
        return False, notes
    if fills_30d < 5:
        notes.append("fills_30d<5")
        return False, notes
    notes.append("deep_dive_ok")
    return True, notes


def proxy_data_completeness(
    *,
    fills_30d: int,
    pnl_snapshots_30d: int,
    has_behavior_score: bool,
) -> float:
    """Rough 0–100 coverage score until dedicated completeness job exists."""

    fill_pts = min(fills_30d / 100.0, 1.0) * 35.0
    snap_pts = min(pnl_snapshots_30d / 30.0, 1.0) * 40.0
    score_pts = 15.0 if has_behavior_score else 0.0
    behavior_pts = 10.0 if has_behavior_score else min(fills_30d / 50.0, 1.0) * 10.0
    return min(100.0, fill_pts + snap_pts + score_pts + behavior_pts)


def proxy_avg_confidence(
    *,
    fills_30d: int,
    pnl_snapshots_30d: int,
    entry_timing_samples_30d: int,
) -> float:
    """sqrt(samples/target) averaged across five pillar proxies (spec step 5)."""

    targets = (50, 30, 20, 20, 25)
    samples = (
        fills_30d,
        pnl_snapshots_30d,
        pnl_snapshots_30d,
        fills_30d,
        entry_timing_samples_30d,
    )
    confs = [min(1.0, math.sqrt(s / t)) if t > 0 else 0.0 for s, t in zip(samples, targets, strict=True)]
    return sum(confs) / len(confs)
