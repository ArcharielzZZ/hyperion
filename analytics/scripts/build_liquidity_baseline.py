#!/usr/bin/env python3
"""Rebuild liquidity baseline from local s3_cache (0 GB S3)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.lib.liquidity_baseline import BASELINE_PATH, build_baseline_df, save_baseline


def main() -> int:
    df = build_baseline_df()
    if df.is_empty():
        print("Keine s3_cache Metriken gefunden — Baseline leer.")
        return 1
    path = save_baseline(df)
    print(f"Baseline: {len(df)} (coin, utc_hour) Zeilen -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
