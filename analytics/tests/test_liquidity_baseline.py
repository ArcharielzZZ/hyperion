"""Tests for liquidity baseline and z_spread from s3_cache."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import polars as pl
import pytest

from analytics.lib import liquidity_baseline as lb


def test_ensure_spread_bps_derives_from_spread_mid():
    df = pl.DataFrame(
        {
            "spread": [1.0],
            "mid_px": [100.0],
        }
    )
    out = lb.ensure_spread_bps(df)
    assert out["spread_bps"][0] == pytest.approx(100.0)


def test_build_baseline_and_z_spread(tmp_path, monkeypatch):
    cache_root = tmp_path / "s3_cache"
    coin_dir = cache_root / "BTC" / "20250311"
    coin_dir.mkdir(parents=True)

    ts = datetime(2025, 3, 11, 9, 0, tzinfo=timezone.utc)
    rows = []
    for i, bps in enumerate([10.0, 12.0, 11.0, 13.0, 10.5, 11.5]):
        rows.append(
            {
                "snapshot_timestamp": ts,
                "spread": 1.0,
                "mid_px": 100.0,
                "spread_bps": bps,
            }
        )
    pl.DataFrame(rows).write_parquet(coin_dir / "9_metrics.parquet")

    monkeypatch.setattr(lb, "S3_CACHE_DIR", cache_root)
    monkeypatch.setattr(lb, "BASELINE_DIR", cache_root / "_baseline")
    monkeypatch.setattr(lb, "BASELINE_PATH", cache_root / "_baseline" / "liquidity_baseline.parquet")

    baseline = lb.build_baseline_df()
    assert len(baseline) == 1
    assert baseline["coin"][0] == "BTC"
    assert baseline["utc_hour"][0] == 9
    assert baseline["n_samples"][0] == 6

    fill = pl.DataFrame(
        {
            "timestamp": [datetime(2025, 3, 11, 9, 30, tzinfo=timezone.utc)],
            "spread_bps": [20.0],
        }
    )
    out = lb.attach_z_spread(fill, coin="BTC", baseline=baseline)
    assert out["z_spread"][0] is not None
    mu = float(baseline["mu_spread_bps"][0])
    sigma = float(baseline["sigma_spread_bps"][0])
    expected = (20.0 - mu) / sigma
    assert out["z_spread"][0] == pytest.approx(expected)


def test_z_spread_null_when_baseline_thin(tmp_path, monkeypatch):
    cache_root = tmp_path / "s3_cache"
    coin_dir = cache_root / "ETH" / "20250311"
    coin_dir.mkdir(parents=True)
    ts = datetime(2025, 3, 11, 14, 0, tzinfo=timezone.utc)
    pl.DataFrame(
        {
            "snapshot_timestamp": [ts, ts],
            "spread_bps": [5.0, 6.0],
        }
    ).write_parquet(coin_dir / "14_metrics.parquet")

    monkeypatch.setattr(lb, "S3_CACHE_DIR", cache_root)
    baseline = lb.build_baseline_df()
    assert baseline["n_samples"][0] == 2

    fill = pl.DataFrame(
        {
            "timestamp": [datetime(2025, 3, 11, 14, 15, tzinfo=timezone.utc)],
            "spread_bps": [8.0],
        }
    )
    out = lb.attach_z_spread(fill, coin="ETH", baseline=baseline)
    assert out["z_spread"][0] is None
