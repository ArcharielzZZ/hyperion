"""Pydantic domain models — analytics layer (may diverge slightly from OLTP row shapes)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Wallet(BaseModel):
    """Logical wallet identity (maps to ``traders.wallet`` in Postgres)."""

    model_config = ConfigDict(extra="forbid")
    address: str = Field(..., description="0x-prefixed lower-case address")
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class TradeFill(BaseModel):
    """
    Normalized fill suitable for Parquet + merge into ``fills``.

    ``event_key`` must match Rust ingest dedupe semantics when writing OLTP.
    """

    model_config = ConfigDict(extra="forbid")
    wallet: str
    coin: str
    side: str
    size: float
    leverage: float = 0.0
    price: float
    event_timestamp: datetime
    event_key: str
    source: str = "historical_backfill"
    fill_dir: str | None = None
    closed_pnl_usd: float | None = None
    fee_usd: float | None = None
    raw: dict[str, Any] = Field(default_factory=dict, description="Original payload subset for audit")


class PositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wallet: str
    coin: str
    direction: str
    entry_price: float
    size: float
    leverage: float
    unrealized_pnl: float
    event_timestamp: datetime
    snapshot_key: str


class FundingPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wallet: str
    coin: str
    amount: float
    event_timestamp: datetime
    event_key: str


class LiquidationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wallet: str
    coin: str
    side: str
    size: float
    price: float
    event_timestamp: datetime
    event_key: str


class WalletMetrics(BaseModel):
    """
    Rolling-window metrics blob (stored as JSONB in ``pipeline_wallet_metrics``).

    Sub-models are grouped by spec; values are optional for incremental rollout.
    """

    model_config = ConfigDict(extra="allow")

    performance: dict[str, float | None] = Field(default_factory=dict)
    behavior: dict[str, float | None] = Field(default_factory=dict)
    market_context: dict[str, float | None] = Field(default_factory=dict)
    risk: dict[str, float | None] = Field(default_factory=dict)


class RankingSnapshot(BaseModel):
    """In-memory representation before persisting ``pipeline_ranking_snapshots``."""

    model_config = ConfigDict(extra="forbid")
    formula_version: str
    as_of: datetime
    entries: list[dict[str, Any]]
