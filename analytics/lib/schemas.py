"""Shared Polars schema contracts for wallet bundle Parquet files."""

from __future__ import annotations

import polars as pl

FILLS_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "coin": pl.String,
    "side": pl.String,
    "dir": pl.String,
    "price": pl.Float64,
    "size": pl.Float64,
    "fee": pl.Float64,
    "closed_pnl": pl.Float64,
    "hash": pl.String,
}

ORDERS_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "status_timestamp": pl.Datetime("ms"),
    "coin": pl.String,
    "limit_px": pl.Float64,
    "size": pl.Float64,
    "orig_size": pl.Float64,
    "order_type": pl.String,
    "status": pl.String,
    "side": pl.String,
    "oid": pl.Int64,
    "reduce_only": pl.Boolean,
}

USER_FUNDING_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "coin": pl.String,
    "usdc": pl.Float64,
    "szi": pl.Float64,
    "funding_rate": pl.Float64,
    "hash": pl.String,
}

LEDGER_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "ledger_type": pl.String,
    "usdc": pl.Float64,
    "fee": pl.Float64,
    "hash": pl.String,
}

CANDLES_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}

FUNDING_HISTORY_SCHEMA: dict[str, pl.DataType] = {
    "timestamp": pl.Datetime("ms"),
    "coin": pl.String,
    "funding_rate": pl.Float64,
    "premium": pl.Float64,
}

PREPARED_LIMIT_ORDERS_SCHEMA: dict[str, pl.DataType] = {
    **ORDERS_SCHEMA,
    "order_kind": pl.String,
    "ambiguous": pl.Boolean,
}
