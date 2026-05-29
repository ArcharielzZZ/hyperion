"""Liquidity baseline (spread_bps z-scores) from local s3_cache — no S3 downloads."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from analytics.lib.parquet_io import atomic_write_parquet

REPO_ROOT = Path(__file__).resolve().parents[2]
S3_CACHE_DIR = REPO_ROOT / "analytics" / "data_lake" / "s3_cache"
BASELINE_DIR = S3_CACHE_DIR / "_baseline"
BASELINE_PATH = BASELINE_DIR / "liquidity_baseline.parquet"

MIN_SAMPLES = 5
SIGMA_EPS = 1e-6

BASELINE_SCHEMA: dict[str, pl.DataType] = {
    "coin": pl.String,
    "utc_hour": pl.Int8,
    "mu_spread_bps": pl.Float64,
    "sigma_spread_bps": pl.Float64,
    "n_samples": pl.UInt32,
}


def ensure_spread_bps(df: pl.DataFrame) -> pl.DataFrame:
    """Derive spread_bps from spread/mid_px when column missing (legacy cache)."""
    if df.is_empty():
        return df
    if "spread_bps" in df.columns and df["spread_bps"].is_not_null().any():
        return df
    if "spread" not in df.columns or "mid_px" not in df.columns:
        return df
    return df.with_columns(
        pl.when(pl.col("mid_px") > 0)
        .then(pl.col("spread") / pl.col("mid_px") * 10_000.0)
        .otherwise(None)
        .alias("spread_bps")
    )


def _metrics_glob() -> list[Path]:
    return sorted(S3_CACHE_DIR.glob("*/*/*_metrics.parquet"))


def _coin_from_metrics_path(path: Path) -> str:
    # s3_cache/<COIN>/<YYYYMMDD>/<H>_metrics.parquet
    return path.parent.parent.name


def scan_cache_metrics() -> pl.DataFrame:
    """Load all hour metrics from s3_cache with coin + utc_hour."""
    paths = _metrics_glob()
    if not paths:
        return pl.DataFrame(schema={
            "coin": pl.String,
            "utc_hour": pl.Int8,
            "spread_bps": pl.Float64,
        })

    parts: list[pl.DataFrame] = []
    for path in paths:
        coin = _coin_from_metrics_path(path)
        df = pl.read_parquet(path)
        df = ensure_spread_bps(df)
        if "spread_bps" not in df.columns or "snapshot_timestamp" not in df.columns:
            continue
        part = (
            df.select(
                pl.lit(coin).alias("coin"),
                pl.col("snapshot_timestamp").dt.hour().cast(pl.Int8).alias("utc_hour"),
                pl.col("spread_bps"),
            )
            .filter(pl.col("spread_bps").is_not_null())
        )
        if not part.is_empty():
            parts.append(part)

    if not parts:
        return pl.DataFrame(schema={
            "coin": pl.String,
            "utc_hour": pl.Int8,
            "spread_bps": pl.Float64,
        })
    return pl.concat(parts, how="vertical_relaxed")


def build_baseline_df() -> pl.DataFrame:
    """Aggregate (coin, utc_hour) mu/sigma from cached hour metrics."""
    metrics = scan_cache_metrics()
    if metrics.is_empty():
        return pl.DataFrame(schema=BASELINE_SCHEMA)

    return (
        metrics.group_by("coin", "utc_hour")
        .agg(
            pl.col("spread_bps").mean().alias("mu_spread_bps"),
            pl.col("spread_bps").std().alias("sigma_spread_bps"),
            pl.len().alias("n_samples"),
        )
        .with_columns(pl.col("n_samples").cast(pl.UInt32))
    )


def save_baseline(df: pl.DataFrame) -> Path:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(df, BASELINE_PATH, compression="zstd")
    return BASELINE_PATH


def load_baseline() -> pl.DataFrame:
    if not BASELINE_PATH.exists():
        return pl.DataFrame(schema=BASELINE_SCHEMA)
    return pl.read_parquet(BASELINE_PATH)


def _cache_newer_than_baseline() -> bool:
    if not BASELINE_PATH.exists():
        return True
    baseline_mtime = BASELINE_PATH.stat().st_mtime
    for path in _metrics_glob():
        if path.stat().st_mtime > baseline_mtime:
            return True
    return False


def ensure_baseline(*, force: bool = False) -> pl.DataFrame:
    """Build or reload baseline parquet when cache changed."""
    if force or not BASELINE_PATH.exists() or _cache_newer_than_baseline():
        df = build_baseline_df()
        if not df.is_empty():
            save_baseline(df)
        return df
    return load_baseline()


def invalidate_baseline() -> None:
    """Remove persisted baseline so next ensure_baseline() rebuilds."""
    if BASELINE_PATH.exists():
        BASELINE_PATH.unlink()


def attach_z_spread(
    df: pl.DataFrame,
    *,
    coin: str,
    timestamp_col: str = "timestamp",
    spread_bps_col: str = "spread_bps",
    baseline: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Join z_spread per row using (coin, utc_hour) baseline lookup."""
    if df.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("z_spread"))

    df = ensure_spread_bps(df)
    if spread_bps_col not in df.columns:
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("z_spread"))

    if baseline is None:
        baseline = ensure_baseline()

    if baseline.is_empty():
        return df.with_columns(pl.lit(None).cast(pl.Float64).alias("z_spread"))

    bl = baseline.filter(pl.col("coin") == coin).select(
        "utc_hour",
        "mu_spread_bps",
        "sigma_spread_bps",
        "n_samples",
    )

    out = df.with_columns(
        pl.col(timestamp_col).dt.hour().cast(pl.Int8).alias("_utc_hour")
    ).join(bl, left_on="_utc_hour", right_on="utc_hour", how="left")

    out = out.with_columns(
        pl.when(
            pl.col("n_samples").is_not_null()
            & (pl.col("n_samples") >= MIN_SAMPLES)
            & pl.col(spread_bps_col).is_not_null()
            & pl.col("mu_spread_bps").is_not_null()
        )
        .then(
            (pl.col(spread_bps_col) - pl.col("mu_spread_bps"))
            / pl.max_horizontal(pl.col("sigma_spread_bps").fill_null(0), pl.lit(SIGMA_EPS))
        )
        .otherwise(None)
        .alias("z_spread")
    )

    drop_cols = [c for c in ("_utc_hour", "utc_hour", "mu_spread_bps", "sigma_spread_bps", "n_samples") if c in out.columns]
    return out.drop(drop_cols)


def format_z_spread_note(baseline: pl.DataFrame, coin: str) -> str | None:
    """Short hint when baseline is thin for a coin."""
    if baseline.is_empty():
        return "Baseline: noch keine s3_cache-Daten"
    sub = baseline.filter(pl.col("coin") == coin)
    if sub.is_empty():
        return f"Baseline: keine Stunden fuer {coin} im Cache"
    thin = sub.filter(pl.col("n_samples") < MIN_SAMPLES)
    if len(thin) == len(sub):
        return f"Baseline duenn ({len(sub)} UTC-Stunden, <{MIN_SAMPLES} Samples/h)"
    return None
