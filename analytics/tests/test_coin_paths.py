"""Tests for HIP-3 / colon coin filename sanitization."""

from __future__ import annotations

from pathlib import Path

from analytics.lib.coin_paths import (
    market_candles_filename,
    market_candles_path,
    market_funding_filename,
    sanitize_coin_for_filename,
)


def test_colon_coin_sanitized_for_windows():
    assert sanitize_coin_for_filename("xyz:GOLD") == "xyz__GOLD"
    assert ":" not in market_candles_filename("xyz:GOLD", "4h")
    assert market_candles_filename("xyz:GOLD", "4h") == "xyz__GOLD_candles_4h.parquet"


def test_plain_coin_unchanged():
    assert sanitize_coin_for_filename("BTC") == "BTC"
    assert market_funding_filename("ETH") == "ETH_funding.parquet"


def test_market_path_builds_under_market_dir():
    p = market_candles_path(Path("/bundle/market"), "xyz:CL", "4h")
    assert p.name == "xyz__CL_candles_4h.parquet"
    assert p.parent.name == "market"
