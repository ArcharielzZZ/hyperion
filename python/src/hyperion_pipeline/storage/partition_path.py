"""Hive-style paths for Parquet datasets (partition pruning)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def fill_partition_dir(base: Path, ts: datetime) -> Path:
    """Return ``.../fills/year=YYYY/month=MM/day=DD`` (UTC calendar components)."""

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    utc = ts.astimezone(timezone.utc)
    return base / "fills" / f"year={utc.year:04d}" / f"month={utc.month:02d}" / f"day={utc.day:02d}"
