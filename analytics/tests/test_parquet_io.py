"""Tests for atomic Parquet writes."""

from __future__ import annotations

import polars as pl

from analytics.lib.parquet_io import atomic_write_parquet


def test_atomic_write_parquet_creates_target(tmp_path):
    path = tmp_path / "data.parquet"
    df = pl.DataFrame({"x": [1, 2]})
    atomic_write_parquet(df, path, compression="zstd")
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert pl.read_parquet(path)["x"].to_list() == [1, 2]


def test_atomic_write_parquet_replaces_existing(tmp_path):
    path = tmp_path / "data.parquet"
    atomic_write_parquet(pl.DataFrame({"x": [1]}), path)
    atomic_write_parquet(pl.DataFrame({"x": [9]}), path)
    assert pl.read_parquet(path)["x"].to_list() == [9]
