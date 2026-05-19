"""Orchestrate wallet filter stages 0–3."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hyperion_pipeline.models.pydantic_domain import TradeFill
from hyperion_pipeline.wallet_filter.aggregates import build_daily_stats, multi_window_from_fills
from hyperion_pipeline.wallet_filter.config import WalletFilterConfig
from hyperion_pipeline.wallet_filter.discovery import discovery_score_promoted
from hyperion_pipeline.wallet_filter.models import WalletFilterResult, WalletStatus
from hyperion_pipeline.wallet_filter.stages import (
    label_to_status,
    proxy_avg_confidence,
    proxy_data_completeness,
    stage0_route,
    stage1_hf_label,
    stage2_promotion,
    stage3_deep_dive_eligible,
)
from hyperion_pipeline.wallet_filter.tiers import assign_behavior_tier


def evaluate_wallet_filter(
    wallet: str,
    fills: list[TradeFill],
    *,
    cfg: WalletFilterConfig | None = None,
    existing_status: WalletStatus | None = None,
    force_whale_promoted: bool = False,
    pnl_snapshots_30d: int = 0,
    entry_timing_samples_30d: int = 0,
    behavior_score: float | None = None,
    scoring_runs: int = 0,
    history_stability: float = 0.75,
    as_of: datetime | None = None,
) -> WalletFilterResult:
    cfg = cfg or WalletFilterConfig.from_env()
    w = wallet.lower()
    notes: list[str] = []

    action, blocked = stage0_route(
        existing_status=existing_status,
        force_whale_promoted=force_whale_promoted,
    )
    if blocked:
        return WalletFilterResult(
            wallet=w,
            status="BANNED",
            label="HIGH_FREQUENCY",
            stage_stopped=0,
            promoted=False,
            banned=True,
            deep_dive_eligible=False,
            behavior_tier=None,
            discovery_score=None,
            avg_confidence=None,
            behavior_score=behavior_score,
            data_completeness_pct=None,
            notes=["stage0_blocked"],
        )

    anchor = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_start = anchor - timedelta(days=30)
    fills_30d = [f for f in fills if f.event_timestamp.astimezone(timezone.utc) >= window_start]
    trade_count_24h = sum(
        1
        for f in fills
        if f.event_timestamp.astimezone(timezone.utc) >= anchor - timedelta(hours=24)
    )
    active_days_7d = len(
        {
            f.event_timestamp.astimezone(timezone.utc).date()
            for f in fills
            if f.event_timestamp.astimezone(timezone.utc) >= anchor - timedelta(days=7)
        }
    )

    if action == "promote_fast_path" and existing_status == "PROMOTED":
        mw = multi_window_from_fills(fills, as_of=anchor)
        return _finalize_promoted(
            w,
            mw=mw,
            label="NORMAL",
            cfg=cfg,
            fills_30d=len(fills_30d),
            pnl_snapshots_30d=pnl_snapshots_30d,
            entry_timing_samples_30d=entry_timing_samples_30d,
            behavior_score=behavior_score,
            scoring_runs=scoring_runs,
            history_stability=history_stability,
            trade_count_24h=trade_count_24h,
            active_days_7d=active_days_7d,
            notes=["stage0_promoted_fast_path"],
            stage_stopped=0,
            as_of=anchor,
        )

    label = stage1_hf_label(fills, cfg)
    status = label_to_status(label)
    if status == "BANNED":
        return WalletFilterResult(
            wallet=w,
            status="BANNED",
            label=label,
            stage_stopped=1,
            promoted=False,
            banned=True,
            deep_dive_eligible=False,
            behavior_tier=None,
            discovery_score=None,
            avg_confidence=None,
            behavior_score=behavior_score,
            data_completeness_pct=None,
            notes=[f"stage1_{label}"],
        )

    mw = multi_window_from_fills(fills, as_of=anchor)
    promoted, s2_notes = stage2_promotion(mw, cfg, as_of=anchor)
    notes.extend(s2_notes)

    if force_whale_promoted:
        promoted = True
        notes.append("whale_registry_override")
        if mw.total_trades < cfg.min_trades:
            notes.append("whale_min_trades_override")

    if not promoted:
        return WalletFilterResult(
            wallet=w,
            status="PENDING",
            label=label,
            stage_stopped=2,
            promoted=False,
            banned=False,
            deep_dive_eligible=False,
            behavior_tier=None,
            discovery_score=None,
            avg_confidence=None,
            behavior_score=behavior_score,
            data_completeness_pct=proxy_data_completeness(
                fills_30d=len(fills_30d),
                pnl_snapshots_30d=pnl_snapshots_30d,
                has_behavior_score=behavior_score is not None,
            ),
            multi_window=mw,
            notes=notes,
        )

    return _finalize_promoted(
        w,
        mw=mw,
        label=label,
        cfg=cfg,
        fills_30d=len(fills_30d),
        pnl_snapshots_30d=pnl_snapshots_30d,
        entry_timing_samples_30d=entry_timing_samples_30d,
        behavior_score=behavior_score,
        scoring_runs=scoring_runs,
        history_stability=history_stability,
        trade_count_24h=trade_count_24h,
        active_days_7d=active_days_7d,
        notes=notes,
        stage_stopped=3,
        as_of=anchor,
    )


def _finalize_promoted(
    wallet: str,
    *,
    mw,
    label,
    cfg: WalletFilterConfig,
    fills_30d: int,
    pnl_snapshots_30d: int,
    entry_timing_samples_30d: int,
    behavior_score: float | None,
    scoring_runs: int,
    history_stability: float,
    trade_count_24h: int,
    active_days_7d: int,
    notes: list[str],
    stage_stopped: int,
    as_of: datetime,
) -> WalletFilterResult:
    comp = proxy_data_completeness(
        fills_30d=fills_30d,
        pnl_snapshots_30d=pnl_snapshots_30d,
        has_behavior_score=behavior_score is not None,
    )
    conf = proxy_avg_confidence(
        fills_30d=fills_30d,
        pnl_snapshots_30d=pnl_snapshots_30d,
        entry_timing_samples_30d=entry_timing_samples_30d,
    )
    deep_ok, deep_notes = stage3_deep_dive_eligible(
        pnl_snapshots_30d=pnl_snapshots_30d,
        fills_30d=fills_30d,
        data_completeness_pct=comp,
        avg_confidence=conf,
        cfg=cfg,
    )
    notes.extend(deep_notes)

    inactive_days = 0.0
    if mw.last_trade_at is not None:
        inactive_days = (as_of - mw.last_trade_at).total_seconds() / 86_400.0

    status: WalletStatus = "PROMOTED"
    if inactive_days > cfg.archive_inactive_days and "whale_registry_override" not in notes:
        status = "ARCHIVED"
        notes.append(f"archived_inactive_{inactive_days:.0f}d")

    disc = discovery_score_promoted(
        data_completeness_pct=comp,
        pnl_snapshots_30d=pnl_snapshots_30d,
        fills_30d=fills_30d,
        has_behavior_score=behavior_score is not None,
        last_trade_at=mw.last_trade_at,
        trade_count_24h=trade_count_24h,
        active_days_7d=active_days_7d,
        scoring_runs=scoring_runs,
        history_stability=history_stability,
        cfg=cfg,
        as_of=as_of,
    )

    tier = assign_behavior_tier(
        behavior_score=behavior_score,
        avg_confidence=conf,
        data_completeness_pct=comp,
        cfg=cfg,
    ) if deep_ok and status == "PROMOTED" else ("watchlist" if status == "PROMOTED" else None)

    return WalletFilterResult(
        wallet=wallet,
        status=status,
        label=label,
        stage_stopped=stage_stopped,
        promoted=status == "PROMOTED",
        banned=False,
        deep_dive_eligible=deep_ok,
        behavior_tier=tier,
        discovery_score=disc,
        avg_confidence=conf,
        behavior_score=behavior_score,
        data_completeness_pct=comp,
        multi_window=mw,
        notes=notes,
    )


def daily_rows_for_persist(fills: list[TradeFill]) -> dict:
    """Expose daily stats for DB upsert."""
    return build_daily_stats(fills)
