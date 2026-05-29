"""Tests for order book chart bucket joins."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl
import pytest

from orderbook_pulls import join_orderbook_to_buckets

EMPTY_BASELINE = pl.DataFrame(
    schema={
        "coin": pl.String,
        "utc_hour": pl.Int8,
        "mu_spread_bps": pl.Float64,
        "sigma_spread_bps": pl.Float64,
        "n_samples": pl.UInt32,
    }
)


def test_join_orderbook_to_buckets_adds_hover_columns():
    fill_ts = datetime(2025, 3, 11, 9, 0, tzinfo=timezone.utc)
    bucket_ts = datetime(2025, 3, 11, 8, 0, tzinfo=timezone.utc)
    buckets = pl.DataFrame({"timestamp": [bucket_ts], "open_long_n": [1]})
    fills = pl.DataFrame(
        {
            "timestamp": [fill_ts],
            "coin": ["BTC"],
            "hash": ["h1"],
            "dir": ["Open Long"],
            "size": [1.0],
        }
    )
    orderbook = pl.DataFrame(
        {
            "fill_hash": ["h1"],
            "spread": [0.12],
            "mid_px": [100.0],
            "bid_depth_top5": [1200.0],
            "ask_depth_top5": [980.0],
            "bid_levels_json": [json.dumps([{"px": 100.0, "sz": 1.0, "n": 2}])],
            "ask_levels_json": [json.dumps([{"px": 100.5, "sz": 1.5, "n": 3}])],
        }
    )
    out = join_orderbook_to_buckets(
        buckets, fills, orderbook, "BTC", "4h", baseline=EMPTY_BASELINE
    )
    assert "ob_spread_mean" in out.columns
    assert "ob_spread_bps_mean" in out.columns
    assert "ob_z_spread_mean" in out.columns
    assert out["ob_spread_mean"][0] == 0.12
    assert out["ob_bid_levels_json"][0] is not None


def test_join_orderbook_to_buckets_unsorted_fills():
    """Regression: group_by_dynamic requires timestamp sort, not size sort."""
    bucket_ts = datetime(2025, 3, 11, 8, 0, tzinfo=timezone.utc)
    buckets = pl.DataFrame({"timestamp": [bucket_ts], "open_long_n": [1]})
    fills = pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 3, 11, 9, 30, tzinfo=timezone.utc),
                datetime(2025, 3, 11, 9, 0, tzinfo=timezone.utc),
            ],
            "coin": ["BTC", "BTC"],
            "hash": ["h_small", "h_big"],
            "dir": ["Open Long", "Open Long"],
            "size": [0.5, 5.0],
        }
    )
    orderbook = pl.DataFrame(
        {
            "fill_hash": ["h_small", "h_big"],
            "spread": [0.1, 0.2],
            "mid_px": [100.0, 100.0],
            "bid_depth_top5": [100.0, 200.0],
            "ask_depth_top5": [90.0, 180.0],
            "bid_levels_json": [
                json.dumps([{"px": 99.0, "sz": 1.0, "n": 1}]),
                json.dumps([{"px": 100.0, "sz": 2.0, "n": 2}]),
            ],
            "ask_levels_json": [
                json.dumps([{"px": 99.5, "sz": 1.0, "n": 1}]),
                json.dumps([{"px": 100.5, "sz": 2.0, "n": 2}]),
            ],
        }
    )
    out = join_orderbook_to_buckets(
        buckets, fills, orderbook, "BTC", "4h", baseline=EMPTY_BASELINE
    )
    assert out["ob_spread_mean"][0] == pytest.approx(0.15)
    assert '"px": 100.0' in out["ob_bid_levels_json"][0]
