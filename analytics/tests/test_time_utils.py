"""Tests for UTC-safe timestamp helpers."""

from __future__ import annotations

import polars as pl

from analytics.lib.time_utils import (
    candles_cover_bounds,
    coin_time_bounds_from_fills,
    epoch_ms_max,
    epoch_ms_min,
    fills_time_bounds_ms,
)


def test_epoch_ms_min_after_parquet_roundtrip(tmp_path):
    path = tmp_path / "ts.parquet"
    df = pl.DataFrame({"timestamp": pl.Series([1704067200000], dtype=pl.Datetime("ms"))})
    df.write_parquet(path)
    loaded = pl.read_parquet(path)
    assert epoch_ms_min(loaded["timestamp"]) == 1704067200000


def test_epoch_ms_not_local_timezone_offset():
    """Naive .timestamp()*1000 would be wrong on CET; epoch ms must stay UTC."""
    series = pl.Series([1704067200000], dtype=pl.Datetime("ms"))
    assert epoch_ms_min(series) == 1704067200000
    assert epoch_ms_max(series) == 1704067200000


def test_coin_time_bounds_padding():
    df = pl.DataFrame(
        {
            "coin": ["BTC", "BTC"],
            "timestamp": pl.Series(
                [1704067200000, 1704153600000], dtype=pl.Datetime("ms")
            ),
        }
    )
    start, end = coin_time_bounds_from_fills(df, "BTC", pad_ms=1000)
    assert start == 1704067200000 - 1000
    assert end == 1704153600000 + 1000


def test_fills_time_bounds_empty():
    df = pl.DataFrame(schema={"timestamp": pl.Datetime("ms")})
    start, end = fills_time_bounds_ms(df)
    assert start == 0
    assert end > 0


def test_candles_cover_bounds():
    candles = pl.DataFrame(
        {"timestamp": pl.Series([1000, 5000, 9000], dtype=pl.Datetime("ms"))}
    )
    assert candles_cover_bounds(candles, 1000, 9000, slack_ms=0)
    assert not candles_cover_bounds(candles, 0, 9000, slack_ms=0)
    assert candles_cover_bounds(candles, 0, 9000, slack_ms=1000)
