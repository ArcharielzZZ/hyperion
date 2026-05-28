"""Tests for order dedupe and classification."""

from __future__ import annotations

from analytics.lib.hl_fetch import dedupe_order_snapshots, orders_to_dataframe
from wallet_chart import classify_limit_order, is_order_intent_ambiguous


def _order_row(*, status: str, side: str, reduce_only: bool, oid: int = 1) -> dict:
    return {
        "order": {
            "timestamp": 1704067200000,
            "coin": "BTC",
            "limitPx": "50000",
            "sz": "1",
            "origSz": "1",
            "orderType": "Limit",
            "side": side,
            "oid": oid,
            "reduceOnly": reduce_only,
        },
        "status": status,
        "statusTimestamp": 1704067200001,
    }


def test_orders_to_dataframe_dedupes_open_and_filled():
    rows = [
        _order_row(status="open", side="B", reduce_only=False, oid=42),
        _order_row(status="filled", side="B", reduce_only=False, oid=42),
    ]
    df = orders_to_dataframe(rows)
    assert len(df) == 1
    assert df["status"][0].lower() == "filled"


def test_dedupe_prefers_terminal_over_open():
    import polars as pl

    df = pl.DataFrame(
        {
            "timestamp": [1, 2],
            "status_timestamp": [1, 2],
            "coin": ["BTC", "BTC"],
            "limit_px": [1.0, 1.0],
            "size": [1.0, 0.0],
            "orig_size": [1.0, 1.0],
            "order_type": ["Limit", "Limit"],
            "status": ["open", "canceled"],
            "side": ["B", "B"],
            "oid": [7, 7],
            "reduce_only": [False, False],
        }
    ).with_columns(
        [
            pl.from_epoch(pl.col("timestamp"), time_unit="ms").alias("timestamp"),
            pl.from_epoch(pl.col("status_timestamp"), time_unit="ms").alias(
                "status_timestamp"
            ),
        ]
    )
    out = dedupe_order_snapshots(df)
    assert len(out) == 1
    assert out["status"][0].lower() == "canceled"


def test_classify_limit_order_long_open():
    assert classify_limit_order("open", "B", False) == "long_open"
    assert classify_limit_order("open", "B", False, coin="BTC") == "long_open"


def test_classify_limit_order_canceled_short():
    assert classify_limit_order("canceled", "A", False) == "canceled_short"


def test_classify_spot_limit_orders():
    assert classify_limit_order("open", "B", False, coin="@142") == "spot_limit_buy"
    assert classify_limit_order("filled", "A", False, coin="@142") == "spot_limit_sell"
    assert classify_limit_order("canceled", "A", False, coin="@142") == "spot_limit_canceled"
    assert classify_limit_order("open", "A", True, coin="@142") == "spot_limit_close"
    assert (
        classify_limit_order("insufficientSpotBalanceRejected", "A", False, coin="@142")
        == "spot_limit_canceled"
    )


def test_classify_limit_order_unknown_returns_other(caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="wallet_chart")
    assert classify_limit_order("weird", "Z", False) == "other"
    assert any("Unhandled limit order" in r.message for r in caplog.records)


def test_is_order_intent_ambiguous_perp_without_reduce_only():
    assert is_order_intent_ambiguous("BTC", False) is True
    assert is_order_intent_ambiguous("BTC", True) is False
    assert is_order_intent_ambiguous("@142", False) is False


def test_aggregate_order_placement_marks_ambiguous_perp():
    import polars as pl

    from wallet_chart import aggregate_order_placement_markers

    df = pl.DataFrame(
        {
            "timestamp": pl.Series([1704067200000], dtype=pl.Datetime("ms")),
            "status_timestamp": pl.Series([1704153600000], dtype=pl.Datetime("ms")),
            "coin": ["BTC"],
            "limit_px": [70000.0],
            "size": [1.0],
            "orig_size": [1.0],
            "order_type": ["Limit"],
            "status": ["open"],
            "side": ["A"],
            "oid": [1],
            "reduce_only": [False],
        }
    )
    buckets = aggregate_order_placement_markers(df, "4h", coin="BTC")
    assert buckets["short_open_n"][0] == 1
    assert buckets["short_open_ambiguous_n"][0] == 1


def test_aggregate_order_placement_includes_canceled():
    import polars as pl

    from wallet_chart import aggregate_order_placement_markers

    df = pl.DataFrame(
        {
            "timestamp": pl.Series([1704067200000], dtype=pl.Datetime("ms")),
            "status_timestamp": pl.Series([1704153600000], dtype=pl.Datetime("ms")),
            "coin": ["EIGEN"],
            "limit_px": [1.0],
            "size": [1.0],
            "orig_size": [1.0],
            "order_type": ["Limit"],
            "status": ["canceled"],
            "side": ["B"],
            "oid": [1],
            "reduce_only": [False],
        }
    )
    buckets = aggregate_order_placement_markers(df, "4h", coin="EIGEN")
    assert buckets["canceled_n"][0] == 1


def test_aggregate_trades_spot_buy_sell():
    import polars as pl

    from wallet_chart import aggregate_trades

    df = pl.DataFrame(
        {
            "timestamp": pl.Series(
                [1704067200000, 1704067300000], dtype=pl.Datetime("ms")
            ),
            "coin": ["@142", "@142"],
            "dir": ["Buy", "Sell"],
            "size": [1.0, 2.0],
            "price": [100.0, 101.0],
            "closed_pnl": [0.0, 1.0],
        }
    )
    buckets = aggregate_trades(df, "@142", "4h")
    assert buckets["buy_n"].sum() == 1
    assert buckets["sell_n"].sum() == 1
