"""Typed application settings (Pydantic Settings) — dev / staging / production."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(str, Enum):
    """Deployment environment."""

    dev = "dev"
    staging = "staging"
    production = "production"


class Settings(BaseSettings):
    """
    Loads from environment variables and optional ``.env`` files.

    ``DATABASE_URL`` may be ``postgres://`` (Rust style); we normalize to
    ``postgresql+asyncpg://`` for SQLAlchemy asyncio.
    """

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: AppEnv = Field(default=AppEnv.dev, validation_alias="PIPELINE_APP_ENV")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/hyperion",
        validation_alias="DATABASE_URL",
    )
    pipeline_data_dir: Path = Field(default=Path("data"), validation_alias="PIPELINE_DATA_DIR")
    backfill_concurrency: int = Field(default=4, ge=1, le=64, validation_alias="PIPELINE_BACKFILL_CONCURRENCY")
    backfill_chunk_fill_target: int = Field(
        default=5000,
        ge=100,
        description="Soft target rows per chunk before checkpointing.",
        validation_alias="PIPELINE_BACKFILL_CHUNK_SIZE",
    )
    http_timeout_sec: float = Field(default=30.0, validation_alias="PIPELINE_HTTP_TIMEOUT_SEC")
    hyperliquid_info_url: str = Field(
        default="https://api.hyperliquid.xyz/info",
        validation_alias="HYPERLIQUID_INFO_URL",
    )
    trader_last_seen_interval_secs: int = Field(
        default=600,
        ge=1,
        validation_alias="STORAGE_TRADER_LAST_SEEN_UPDATE_SECS",
    )
    hyperliquid_min_request_interval_sec: float = Field(
        default=0.25,
        ge=0.05,
        description="Minimum spacing between Hyperliquid /info POSTs.",
        validation_alias="PIPELINE_HL_MIN_INTERVAL_SEC",
    )
    hyperliquid_tracked_users: str = Field(
        default="",
        validation_alias="HYPERLIQUID_TRACKED_USERS",
        description="Comma-separated 0x addresses ingester subscribes to.",
    )

    risk_per_trade_pct: float = Field(default=0.01, validation_alias="RISK_PER_TRADE_PCT")
    max_position_pct: float = Field(default=0.15, validation_alias="MAX_POSITION_PCT")
    max_total_exposure_pct: float = Field(default=0.50, validation_alias="MAX_TOTAL_EXPOSURE_PCT")
    circuit_breaker_pct: float = Field(default=0.20, validation_alias="CIRCUIT_BREAKER_PCT")
    min_trade_usd: float = Field(default=1.0, validation_alias="MIN_TRADE_USD")
    default_stop_pct: float = Field(default=0.03, validation_alias="DEFAULT_STOP_PCT")
    kelly_fraction: float = Field(default=0.25, validation_alias="KELLY_FRACTION")
    sharpe_window: int = Field(default=30, ge=5, validation_alias="SHARPE_WINDOW")
    rebalance_every_n_trades: int = Field(default=10, ge=1, validation_alias="REBALANCE_EVERY_N_TRADES")
    copy_use_dynamic_sizing: bool = Field(default=True, validation_alias="COPY_USE_DYNAMIC_SIZING")
    ema_span: int = Field(default=20, ge=3, validation_alias="EMA_SPAN")
    # Whale expected edge per trade must be >= multiplier × round-trip cost (fees + slippage).
    # 2.0 is mathematical break-even; 3.5 adds safety margin for variance and durable edge.
    # Raise to 5.0 for a conservative live-bot shortlist. Lower to 2.0 for research.
    edge_cost_multiplier: float = Field(default=3.5, validation_alias="EDGE_COST_MULTIPLIER")
    min_win_rate: float = Field(default=0.45, validation_alias="MIN_WIN_RATE")
    max_size_increase_factor: float = Field(default=1.05, validation_alias="MAX_SIZE_INCREASE_FACTOR")
    # Minimum fraction of FIFO trips that must survive filters to simulate a wallet.
    # Below 30%, the copied subset is too sparse to represent the whale's strategy.
    # Raise to 0.50 for a tighter live-bot shortlist.
    min_copyability: float = Field(default=0.30, validation_alias="MIN_COPYABILITY")
    # Minimum FIFO round trips before we trust win-rate / edge statistics.
    min_trips_detected: int = Field(default=20, ge=1, validation_alias="MIN_TRIPS_DETECTED")

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_asyncpg_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://") :]
        if value.startswith("postgresql://") and "+asyncpg" not in value:
            return "postgresql+asyncpg://" + value[len("postgresql://") :]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings (override in tests via ``get_settings.cache_clear()``)."""

    return Settings()
