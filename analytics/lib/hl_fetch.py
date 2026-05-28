"""Hyperliquid REST helpers for wallet bundle pulls and dashboard fallbacks."""

from __future__ import annotations

import time
from typing import Any

import polars as pl
import requests

from analytics.lib.schemas import (
    CANDLES_SCHEMA,
    FILLS_SCHEMA,
    FUNDING_HISTORY_SCHEMA,
    LEDGER_SCHEMA,
    ORDERS_SCHEMA,
    USER_FUNDING_SCHEMA,
)
from analytics.lib.time_utils import coin_time_bounds_from_fills

HL_API = "https://api.hyperliquid.xyz/info"
HL_HEADERS = {"Content-Type": "application/json"}
HL_TIMEOUT = 30
BATCH_CAP = 2000
FUNDING_BATCH_CAP = 500  # fundingHistory API max per request
PAD_MS = 7 * 24 * 60 * 60 * 1000
POST_MAX_RETRIES = 3
POST_BASE_DELAY_SEC = 1.0

# Hyperliquid returns at most ~5000 candles per request.
INTERVAL_MS: dict[str, int] = {
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
    "1w": 7 * 24 * 60 * 60 * 1000,
    "1M": 30 * 24 * 60 * 60 * 1000,
}
CANDLE_CHUNK_SIZE = 4000
CHUNK_SLEEP_SEC = 0.15
CANDLES_PER_REQUEST = 5000


def _post(payload: dict) -> Any:
    last_exc: Exception | None = None
    for attempt in range(POST_MAX_RETRIES):
        try:
            r = requests.post(
                HL_API, json=payload, headers=HL_HEADERS, timeout=HL_TIMEOUT
            )
            if r.status_code == 429 or r.status_code >= 500:
                r.raise_for_status()
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            if status is not None and status != 429 and status < 500:
                raise
        except requests.RequestException as exc:
            last_exc = exc

        if attempt < POST_MAX_RETRIES - 1:
            time.sleep(POST_BASE_DELAY_SEC * (2**attempt))

    assert last_exc is not None
    raise last_exc


def fetch_fills_all(wallet: str, *, sleep_sec: float = 0.3) -> list[dict]:
    """Paginate userFillsByTime for full wallet history."""
    wallet_l = wallet.lower()
    all_fills: list[dict] = []
    seen_hashes: set[str] = set()
    cur_start = 0
    end_time = int(time.time() * 1000) + 60_000

    while cur_start < end_time:
        batch = _post(
            {
                "type": "userFillsByTime",
                "user": wallet_l,
                "startTime": cur_start,
                "endTime": end_time,
            }
        ) or []

        if not batch:
            break

        fresh = [f for f in batch if f.get("hash") not in seen_hashes]
        for f in fresh:
            h = f.get("hash")
            if h:
                seen_hashes.add(h)
        all_fills.extend(fresh)

        newest_ts = max(f["time"] for f in batch)
        if len(batch) < BATCH_CAP:
            break
        if newest_ts < cur_start:
            break
        cur_start = newest_ts + 1
        time.sleep(sleep_sec)

    return all_fills


def fills_to_dataframe(fills: list[dict]) -> pl.DataFrame:
    if not fills:
        return pl.DataFrame(schema=FILLS_SCHEMA)

    df = pl.DataFrame(fills)
    df = df.with_columns(
        [
            pl.from_epoch(pl.col("time"), time_unit="ms").alias("timestamp"),
            pl.col("px").cast(pl.Float64).alias("price"),
            pl.col("sz").cast(pl.Float64).alias("size"),
            pl.col("fee").cast(pl.Float64).alias("fee"),
            pl.col("closedPnl").cast(pl.Float64).alias("closed_pnl"),
        ]
    )
    df = df.with_columns(
        pl.when(pl.col("dir").str.contains("Long"))
        .then(pl.lit("LONG"))
        .when(pl.col("dir").str.to_lowercase().is_in(["buy"]))
        .then(pl.lit("LONG"))
        .when(pl.col("dir").str.to_lowercase().is_in(["sell"]))
        .then(pl.lit("SHORT"))
        .otherwise(pl.lit("SHORT"))
        .alias("side")
    )
    return df.select(
        ["timestamp", "coin", "side", "dir", "price", "size", "fee", "closed_pnl", "hash"]
    ).sort("timestamp")


def fetch_historical_orders(wallet: str) -> list[dict]:
    """Returns up to ~2000 most recent orders for the wallet."""
    wallet_l = wallet.lower()
    return _post({"type": "historicalOrders", "user": wallet_l}) or []


ORDER_STATUS_PRIORITY: dict[str, int] = {
    "filled": 4,
    "canceled": 3,
    "reduceonlycanceled": 3,
    "margincanceled": 3,
    "triggered": 2,
    "open": 1,
}


def _order_status_priority_expr() -> pl.Expr:
    st = pl.col("status").str.to_lowercase()
    expr = pl.lit(0)
    for status, prio in ORDER_STATUS_PRIORITY.items():
        expr = pl.when(st == status).then(pl.lit(prio)).otherwise(expr)
    return expr.alias("_prio")


def dedupe_order_snapshots(df: pl.DataFrame) -> pl.DataFrame:
    """Keep one row per oid — historicalOrders returns open + terminal snapshots."""
    if df.is_empty() or "oid" not in df.columns:
        return df
    ranked = df.with_columns(_order_status_priority_expr())
    return (
        ranked.sort(["oid", "_prio", "status_timestamp"], descending=[False, True, True])
        .group_by("oid", maintain_order=True)
        .first()
        .drop("_prio")
    )


def orders_to_dataframe(orders: list[dict]) -> pl.DataFrame:
    if not orders:
        return pl.DataFrame(schema=ORDERS_SCHEMA)

    rows = []
    for item in orders:
        o = item.get("order") or {}
        rows.append(
            {
                "timestamp": o.get("timestamp"),
                "status_timestamp": item.get("statusTimestamp"),
                "coin": o.get("coin"),
                "limit_px": o.get("limitPx"),
                "size": o.get("sz"),
                "orig_size": o.get("origSz"),
                "order_type": o.get("orderType"),
                "status": item.get("status"),
                "side": o.get("side"),
                "oid": o.get("oid"),
                "reduce_only": bool(o.get("reduceOnly", False)),
            }
        )

    df = pl.DataFrame(rows)
    df = df.with_columns(
        [
            pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp"),
            pl.from_epoch(pl.col("status_timestamp"), time_unit="ms").alias(
                "status_timestamp"
            ),
            pl.col("limit_px").cast(pl.Float64),
            pl.col("size").cast(pl.Float64),
            pl.col("orig_size").cast(pl.Float64),
            pl.col("oid").cast(pl.Int64),
            pl.col("reduce_only").cast(pl.Boolean),
        ]
    ).sort("timestamp")
    return dedupe_order_snapshots(df)


def fetch_user_funding(
    wallet: str, start_ms: int, end_ms: int, *, sleep_sec: float = 0.2
) -> list[dict]:
    wallet_l = wallet.lower()
    all_rows: list[dict] = []
    cur = start_ms

    while cur <= end_ms:
        batch = _post(
            {
                "type": "userFunding",
                "user": wallet_l,
                "startTime": cur,
                "endTime": end_ms,
            }
        ) or []
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < BATCH_CAP:
            break
        newest = max(r["time"] for r in batch)
        if newest < cur:
            break
        cur = newest + 1
        time.sleep(sleep_sec)

    return all_rows


def user_funding_to_dataframe(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=USER_FUNDING_SCHEMA)

    parsed = []
    for row in rows:
        delta = row.get("delta") or {}
        parsed.append(
            {
                "time": row.get("time"),
                "hash": row.get("hash") or "",
                "coin": delta.get("coin") or "",
                "usdc": delta.get("usdc") or "0",
                "szi": delta.get("szi") or "0",
                "funding_rate": delta.get("fundingRate") or "0",
            }
        )

    df = pl.DataFrame(parsed)
    return df.with_columns(
        [
            pl.from_epoch(pl.col("time"), time_unit="ms").alias("timestamp"),
            pl.col("usdc").cast(pl.Float64),
            pl.col("szi").cast(pl.Float64),
            pl.col("funding_rate").cast(pl.Float64),
        ]
    ).select(["timestamp", "coin", "usdc", "szi", "funding_rate", "hash"]).sort(
        "timestamp"
    )


def fetch_ledger(
    wallet: str, start_ms: int, end_ms: int, *, sleep_sec: float = 0.2
) -> list[dict]:
    wallet_l = wallet.lower()
    all_rows: list[dict] = []
    cur = start_ms

    while cur <= end_ms:
        batch = _post(
            {
                "type": "userNonFundingLedgerUpdates",
                "user": wallet_l,
                "startTime": cur,
                "endTime": end_ms,
            }
        ) or []
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < BATCH_CAP:
            break
        newest = max(r["time"] for r in batch)
        if newest < cur:
            break
        cur = newest + 1
        time.sleep(sleep_sec)

    return all_rows


def ledger_to_dataframe(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=LEDGER_SCHEMA)

    parsed = []
    for row in rows:
        delta = row.get("delta") or {}
        parsed.append(
            {
                "time": row.get("time"),
                "ledger_type": delta.get("type") or "unknown",
                "usdc": delta.get("usdc") or delta.get("amount") or "0",
                "fee": delta.get("fee") or "0",
                "hash": row.get("hash") or "",
            }
        )

    df = pl.DataFrame(parsed)
    return df.with_columns(
        [
            pl.from_epoch(pl.col("time"), time_unit="ms").alias("timestamp"),
            pl.col("usdc").cast(pl.Float64),
            pl.col("fee").cast(pl.Float64),
        ]
    ).select(["timestamp", "ledger_type", "usdc", "fee", "hash"]).sort("timestamp")


def fetch_candles(
    coin: str, interval: str, start_ms: int, end_ms: int
) -> list[dict]:
    out: list[dict] = []
    seen: set[int] = set()
    cur = start_ms

    while cur < end_ms:
        batch = _post(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": cur,
                    "endTime": end_ms,
                },
            }
        ) or []

        if not batch:
            break

        fresh = [c for c in batch if c["t"] not in seen]
        if not fresh:
            break

        for c in fresh:
            seen.add(c["t"])
        out.extend(fresh)

        if len(batch) < CANDLES_PER_REQUEST:
            break
        newest = max(c["t"] for c in fresh)
        if newest <= cur:
            break
        cur = newest + 1

    out.sort(key=lambda c: c["t"])
    return out


def fetch_candles_chunked(
    coin: str, interval: str, start_ms: int, end_ms: int
) -> list[dict]:
    """Fetch full candle range by splitting into API-safe time windows."""
    step_ms = INTERVAL_MS.get(interval)
    if not step_ms or start_ms >= end_ms:
        return fetch_candles(coin, interval, start_ms, end_ms)

    out: list[dict] = []
    seen: set[int] = set()
    chunk_span = CANDLE_CHUNK_SIZE * step_ms
    cur = start_ms

    while cur < end_ms:
        chunk_end = min(end_ms, cur + chunk_span - 1)
        try:
            chunk = fetch_candles(coin, interval, cur, chunk_end)
        except requests.RequestException:
            chunk = []

        for candle in chunk:
            ts = candle["t"]
            if ts not in seen:
                seen.add(ts)
                out.append(candle)

        if chunk_end >= end_ms:
            break
        cur = chunk_end + 1
        time.sleep(CHUNK_SLEEP_SEC)

    out.sort(key=lambda c: c["t"])
    return out


def candles_to_dataframe(candles: list[dict]) -> pl.DataFrame:
    if not candles:
        return pl.DataFrame(schema=CANDLES_SCHEMA)
    df = pl.DataFrame(candles).with_columns(
        [
            pl.from_epoch(pl.col("t"), time_unit="ms").alias("timestamp"),
            pl.col("o").cast(pl.Float64).alias("open"),
            pl.col("h").cast(pl.Float64).alias("high"),
            pl.col("l").cast(pl.Float64).alias("low"),
            pl.col("c").cast(pl.Float64).alias("close"),
            pl.col("v").cast(pl.Float64).alias("volume"),
        ]
    )
    return df.select(
        ["timestamp", "open", "high", "low", "close", "volume"]
    ).sort("timestamp")


def fetch_funding_history(
    coin: str, start_ms: int, end_ms: int, *, sleep_sec: float = 0.2
) -> list[dict]:
    all_rows: list[dict] = []
    cur = start_ms

    while cur <= end_ms:
        batch = _post(
            {
                "type": "fundingHistory",
                "coin": coin,
                "startTime": cur,
                "endTime": end_ms,
            }
        ) or []
        if not batch:
            break
        all_rows.extend(batch)
        if len(batch) < FUNDING_BATCH_CAP:
            break
        newest = max(r["time"] for r in batch)
        if newest < cur:
            break
        cur = newest + 1
        time.sleep(sleep_sec)

    return [r for r in all_rows if r.get("time", 0) <= end_ms]


def funding_history_to_dataframe(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame(schema=FUNDING_HISTORY_SCHEMA)

    df = pl.DataFrame(rows)
    return df.with_columns(
        [
            pl.from_epoch(pl.col("time"), time_unit="ms").alias("timestamp"),
            pl.col("fundingRate").cast(pl.Float64).alias("funding_rate"),
            pl.col("premium").cast(pl.Float64),
        ]
    ).select(["timestamp", "coin", "funding_rate", "premium"]).sort("timestamp")


def coin_time_bounds_ms(fills_df: pl.DataFrame, coin: str) -> tuple[int, int]:
    """Return start/end ms for a coin from fills with 7d padding."""
    return coin_time_bounds_from_fills(fills_df, coin, pad_ms=PAD_MS)
