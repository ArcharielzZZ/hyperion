"""Wallet filter thresholds — env-overridable; interim values differ slightly from SUMMARY_OPTIMIERT."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class WalletFilterConfig:
    """Interim parameters vs spec doc noted in module docstring on ``WalletFilterConfig``."""

    max_fills_sample: int = 10_000
    hf_fill_cap: int = 10_000
    ultra_hf_max_span_days: int = 30
    high_freq_max_span_days: int = 90
    normal_min_history_days: int = 90

    min_volume_usd: float = 500.0
    min_trades: int = 5
    pnl_efficiency_ratio: float = 0.0005
    max_inactive_days: int = 90

    deep_min_pnl_snapshots_30d: int = 12
    deep_min_data_completeness_pct: float = 60.0
    deep_min_avg_confidence: float = 0.35

    tier_high_score: float = 75.0
    tier_high_confidence: float = 0.70
    tier_high_data_completeness_pct: float = 85.0
    tier_active_score: float = 40.0
    tier_active_confidence: float = 0.50
    archive_inactive_days: int = 30
    discovery_cold_start_runs: int = 5

    @classmethod
    def from_env(cls) -> WalletFilterConfig:
        return cls(
            max_fills_sample=_env_int("HYPERION_WALLET_FILTER_MAX_FILLS", 10_000),
            hf_fill_cap=_env_int("HYPERION_WALLET_FILTER_HF_CAP", 10_000),
            ultra_hf_max_span_days=_env_int("HYPERION_WALLET_FILTER_ULTRA_HF_DAYS", 30),
            high_freq_max_span_days=_env_int("HYPERION_WALLET_FILTER_HIGH_FREQ_DAYS", 90),
            normal_min_history_days=_env_int("HYPERION_WALLET_FILTER_NORMAL_HISTORY_DAYS", 90),
            min_volume_usd=_env_float("HYPERION_WALLET_FILTER_MIN_VOLUME_USD", 500.0),
            min_trades=_env_int("HYPERION_WALLET_FILTER_MIN_TRADES", 5),
            pnl_efficiency_ratio=_env_float("HYPERION_WALLET_FILTER_PNL_EFFICIENCY", 0.0005),
            max_inactive_days=_env_int("HYPERION_WALLET_FILTER_MAX_INACTIVE_DAYS", 90),
            deep_min_pnl_snapshots_30d=_env_int("HYPERION_WALLET_FILTER_DEEP_MIN_SNAPSHOTS", 12),
            deep_min_data_completeness_pct=_env_float("HYPERION_WALLET_FILTER_DEEP_MIN_COMPLETENESS", 60.0),
            deep_min_avg_confidence=_env_float("HYPERION_WALLET_FILTER_DEEP_MIN_CONFIDENCE", 0.35),
            tier_high_score=_env_float("HYPERION_WALLET_FILTER_TIER_HIGH_SCORE", 75.0),
            tier_high_confidence=_env_float("HYPERION_WALLET_FILTER_TIER_HIGH_CONF", 0.70),
            tier_high_data_completeness_pct=_env_float(
                "HYPERION_WALLET_FILTER_TIER_HIGH_COMPLETENESS", 85.0
            ),
            tier_active_score=_env_float("HYPERION_WALLET_FILTER_TIER_ACTIVE_SCORE", 40.0),
            tier_active_confidence=_env_float("HYPERION_WALLET_FILTER_TIER_ACTIVE_CONF", 0.50),
            archive_inactive_days=_env_int("HYPERION_WALLET_FILTER_ARCHIVE_DAYS", 30),
            discovery_cold_start_runs=_env_int("HYPERION_WALLET_FILTER_DISCOVERY_COLD_RUNS", 5),
        )
