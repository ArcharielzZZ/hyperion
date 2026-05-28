"""Atomic Parquet writes for analytics data lake artefacts."""

from __future__ import annotations

from pathlib import Path

import polars as pl


def atomic_write_parquet(df: pl.DataFrame, path: Path, **kwargs) -> None:
    """Write Parquet to a temp file, then replace the target atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.write_parquet(tmp, **kwargs)
    tmp.replace(path)
