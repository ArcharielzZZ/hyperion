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
