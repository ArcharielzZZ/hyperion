"""Polars lazy scans over partitioned Parquet (partition pruning)."""

from __future__ import annotations

from pathlib import Path

import polars as pl


def scan_fills_lazy(
    data_root: Path,
    *,
    year: int | None = None,
    month: int | None = None,
    day: int | None = None,
) -> pl.LazyFrame:
    """
    Build a lazy scan over ``fills/year=...`` partitions.

    Pruning: pass ``year``/``month``/``day`` to narrow glob (fewer files touched).
    """

    base = data_root / "fills"
    if year is not None and month is not None and day is not None:
        pattern = str(base / f"year={year:04d}" / f"month={month:02d}" / f"day={day:02d}" / "**/*.parquet")
    elif year is not None and month is not None:
        pattern = str(base / f"year={year:04d}" / f"month={month:02d}" / "**/*.parquet")
    elif year is not None:
        pattern = str(base / f"year={year:04d}" / "**/**/*.parquet")
    else:
        pattern = str(base / "**/**/*.parquet")
    return pl.scan_parquet(pattern)
