"""UTC-safe millisecond timestamps from Polars Datetime columns."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import polars as pl

PAD_MS = 7 * 24 * 60 * 60 * 1000


def epoch_ms_min(series: pl.Series) -> int:
    """Minimum timestamp as UTC epoch milliseconds (avoids local-timezone .timestamp())."""
    return int(series.dt.epoch("ms").min())


def epoch_ms_max(series: pl.Series) -> int:
    """Maximum timestamp as UTC epoch milliseconds."""
    return int(series.dt.epoch("ms").max())


def coin_time_bounds_from_fills(
    fills: pl.DataFrame,
    coin: str,
    *,
    pad_ms: int = PAD_MS,
) -> tuple[int, int]:
    """Return padded start/end ms for a coin based on fill timestamps."""
    subset = fills.filter(pl.col("coin") == coin)
    if subset.is_empty():
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        return 0, now
    t_min = epoch_ms_min(subset["timestamp"])
    t_max = epoch_ms_max(subset["timestamp"])
    return max(0, t_min - pad_ms), t_max + pad_ms


def fills_time_bounds_ms(fills: pl.DataFrame) -> tuple[int, int]:
    """Return min/max fill timestamps in UTC epoch milliseconds."""
    if fills.is_empty():
        return 0, int(time.time() * 1000)
    return epoch_ms_min(fills["timestamp"]), epoch_ms_max(fills["timestamp"])


def candles_cover_bounds(
    candles: pl.DataFrame,
    start_ms: int,
    end_ms: int,
    *,
    slack_ms: int = 0,
) -> bool:
    """True when cached candles span the requested window (within slack)."""
    if candles.is_empty():
        return False
    c_min = epoch_ms_min(candles["timestamp"])
    c_max = epoch_ms_max(candles["timestamp"])
    return c_min <= start_ms + slack_ms and c_max >= end_ms - slack_ms
