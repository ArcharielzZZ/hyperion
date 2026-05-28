"""
Wallet bundle helpers used by the wallet_explorer dashboard.

Reads/writes the same Parquet/JSON artefacts produced by
``analytics/scripts/force_pull_wallet_bundle.py``.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

import polars as pl

from analytics.lib.coin_paths import market_candles_path, market_funding_path
from analytics.lib.parquet_io import atomic_write_parquet
from analytics.lib.schemas import (
    CANDLES_SCHEMA,
    LEDGER_SCHEMA,
    ORDERS_SCHEMA,
    USER_FUNDING_SCHEMA,
)
from analytics.lib.time_utils import PAD_MS, coin_time_bounds_from_fills

REPO_ROOT = Path(__file__).resolve().parents[2]
WALLETS_DIR = REPO_ROOT / "analytics" / "data_lake" / "wallets"
DEFAULT_INTERVAL = "4h"

_CACHE_FUNCS: list[Any] = []


def _cached_loader(func):
    wrapped = functools.lru_cache(maxsize=32)(func)
    _CACHE_FUNCS.append(wrapped)
    return wrapped


def clear_wallet_cache(wallet: str | None = None) -> None:
    """Drop cached Parquet reads (all wallets; per-key eviction not supported)."""
    del wallet
    for fn in _CACHE_FUNCS:
        fn.cache_clear()


def bundle_dir(wallet: str) -> Path:
    return WALLETS_DIR / wallet.lower()


def legacy_fills_path(wallet: str) -> Path:
    return WALLETS_DIR / f"{wallet.lower()}.parquet"


def has_bundle(wallet: str) -> bool:
    return (bundle_dir(wallet) / "fills.parquet").exists()


def has_wallet_data(wallet: str) -> bool:
    return has_bundle(wallet) or legacy_fills_path(wallet).exists()


def load_status(wallet: str) -> dict:
    path = bundle_dir(wallet) / ".pull_status.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_meta(wallet: str) -> dict:
    path = bundle_dir(wallet) / "meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def market_failure_for_coin(wallet: str, coin: str) -> dict[str, str] | None:
    """Return market pull failure entry for a coin, if any."""
    for entry in load_meta(wallet).get("market_failures") or []:
        if entry.get("coin") == coin:
            return entry
    return None


def bundle_stats(wallet: str, fills: pl.DataFrame) -> dict[str, Any]:
    """Return bundle metadata for dashboard display (no UI strings)."""
    meta = load_meta(wallet)
    orders_path = bundle_dir(wallet) / "orders.parquet"
    order_count = 0
    if orders_path.exists():
        try:
            order_count = len(pl.read_parquet(orders_path, columns=["oid"]))
        except Exception:
            order_count = 0

    return {
        "fill_count": len(fills),
        "coin_count": fills["coin"].n_unique() if not fills.is_empty() else 0,
        "pulled_at": meta.get("pulled_at", ""),
        "orders_note": meta.get("orders_note", ""),
        "is_bundle": bool(meta.get("pulled_at")),
        "order_count": order_count,
        "time_min": fills["timestamp"].min() if not fills.is_empty() else None,
        "time_max": fills["timestamp"].max() if not fills.is_empty() else None,
        "market_failures": meta.get("market_failures") or [],
    }


@_cached_loader
def load_fills(wallet: str) -> pl.DataFrame:
    w = wallet.lower()
    bundle_path = bundle_dir(w) / "fills.parquet"
    if bundle_path.exists():
        return pl.read_parquet(bundle_path).sort("timestamp")
    legacy = legacy_fills_path(w)
    if legacy.exists():
        return pl.read_parquet(legacy).sort("timestamp")
    raise FileNotFoundError(f"No fills for wallet {w}")


@_cached_loader
def load_orders(wallet: str) -> pl.DataFrame:
    path = bundle_dir(wallet) / "orders.parquet"
    if not path.exists():
        return pl.DataFrame(schema=ORDERS_SCHEMA)
    df = pl.read_parquet(path).sort("timestamp")
    if "reduce_only" not in df.columns:
        df = df.with_columns(pl.lit(False).alias("reduce_only"))
    return df


@_cached_loader
def load_user_funding(wallet: str) -> pl.DataFrame:
    path = bundle_dir(wallet) / "user_funding.parquet"
    if not path.exists():
        return pl.DataFrame(schema=USER_FUNDING_SCHEMA)
    return pl.read_parquet(path).sort("timestamp")


@_cached_loader
def load_ledger(wallet: str) -> pl.DataFrame:
    path = bundle_dir(wallet) / "ledger.parquet"
    if not path.exists():
        return pl.DataFrame(schema=LEDGER_SCHEMA)
    return pl.read_parquet(path).sort("timestamp")


@_cached_loader
def load_candles(wallet: str, coin: str, interval: str) -> pl.DataFrame | None:
    path = market_candles_path(bundle_dir(wallet) / "market", coin, interval)
    if not path.exists():
        return None
    return pl.read_parquet(path).sort("timestamp")


def save_candles(wallet: str, coin: str, interval: str, df: pl.DataFrame) -> None:
    """Persist fetched candles so 15m/1h etc. do not re-download every chart refresh."""
    market_dir = bundle_dir(wallet) / "market"
    market_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_parquet(
        df.select(list(CANDLES_SCHEMA.keys())),
        market_candles_path(market_dir, coin, interval),
        compression="zstd",
    )


def load_market_funding(wallet: str, coin: str) -> pl.DataFrame | None:
    path = market_funding_path(bundle_dir(wallet), coin)
    if not path.exists():
        return None
    return pl.read_parquet(path).sort("timestamp")


def coin_time_bounds(fills: pl.DataFrame, coin: str) -> tuple[int, int]:
    return coin_time_bounds_from_fills(fills, coin, pad_ms=PAD_MS)
