"""Wallet filter pipeline result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

WalletStatus = Literal["PENDING", "PROMOTED", "BANNED", "ARCHIVED"]
WalletLabel = Literal["NEW_WALLET", "NORMAL", "HIGH_FREQUENCY", "ULTRA_HF_BOT"]
BehaviorTier = Literal["high_quality", "active_tracked", "watchlist"] | None


@dataclass
class MultiWindowPnL:
    pnl_7d: float = 0.0
    trades_7d: int = 0
    pnl_1m: float = 0.0
    trades_1m: int = 0
    pnl_3m: float = 0.0
    trades_3m: int = 0
    pnl_6m: float = 0.0
    trades_6m: int = 0
    total_volume: float = 0.0
    total_trades: int = 0
    last_trade_at: datetime | None = None


@dataclass
class WalletFilterResult:
    wallet: str
    status: WalletStatus
    label: WalletLabel
    stage_stopped: int
    promoted: bool
    banned: bool
    deep_dive_eligible: bool
    behavior_tier: BehaviorTier
    discovery_score: float | None
    avg_confidence: float | None
    behavior_score: float | None
    data_completeness_pct: float | None
    multi_window: MultiWindowPnL = field(default_factory=MultiWindowPnL)
    notes: list[str] = field(default_factory=list)
