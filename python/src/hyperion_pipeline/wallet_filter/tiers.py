"""Behavior tier assignment for PROMOTED wallets."""

from __future__ import annotations

from hyperion_pipeline.wallet_filter.config import WalletFilterConfig
from hyperion_pipeline.wallet_filter.models import BehaviorTier


def assign_behavior_tier(
    *,
    behavior_score: float | None,
    avg_confidence: float | None,
    data_completeness_pct: float | None,
    cfg: WalletFilterConfig,
) -> BehaviorTier:
    if behavior_score is None:
        return "watchlist"

    score = float(behavior_score)
    conf = float(avg_confidence or 0.0)
    comp = float(data_completeness_pct or 0.0)

    if (
        score >= cfg.tier_high_score
        and conf >= cfg.tier_high_confidence
        and comp >= cfg.tier_high_data_completeness_pct
    ):
        return "high_quality"
    if score >= cfg.tier_active_score and conf >= cfg.tier_active_confidence:
        return "active_tracked"
    return "watchlist"
