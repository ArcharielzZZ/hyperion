"""Tests for S3 order book parsing and hour planning."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import polars as pl
import pytest

from analytics.lib.s3_orderbook import (
    HourKey,
    hour_keys_from_timestamps_ms,
    iter_l2_records,
    metrics_from_levels,
    snapshots_for_fills,
)


def test_hour_keys_from_timestamps_ms_unique_hours():
    ts1 = int(datetime(2025, 3, 11, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
    ts2 = int(datetime(2025, 3, 11, 9, 45, tzinfo=timezone.utc).timestamp() * 1000)
    ts3 = int(datetime(2025, 3, 11, 10, 5, tzinfo=timezone.utc).timestamp() * 1000)
    keys = hour_keys_from_timestamps_ms([ts1, ts2, ts3])
    assert keys == [
        HourKey("20250311", 9),
        HourKey("20250311", 10),
    ]


def test_hour_key_s3_path():
    hk = HourKey("20250311", 9)
    assert hk.s3_key("BTC") == "market_data/20250311/9/l2Book/BTC.lz4"


def test_metrics_from_levels_basic():
    data = {
        "time": 1741683599768,
        "levels": [
            [{"px": "100.0", "sz": "2.0", "n": 1}, {"px": "99.5", "sz": "1.0", "n": 1}],
            [{"px": "100.5", "sz": "3.0", "n": 1}, {"px": "101.0", "sz": "1.5", "n": 1}],
        ],
    }
    m = metrics_from_levels(data)
    assert m is not None
    assert m["best_bid_px"] == 100.0
    assert m["best_ask_px"] == 100.5
    assert m["spread"] == 0.5
    assert m["spread_bps"] == pytest.approx(0.5 / 100.25 * 10_000.0)
    assert m["bid_depth_top5"] == 3.0
    assert m["ask_depth_top5"] == 4.5
    bids = json.loads(m["bid_levels_json"])
    assert len(bids) == 2
    assert bids[0]["px"] == 100.0


def test_iter_l2_records_json_line():
    raw = (
        '{"channel":"l2Book","data":{"coin":"BTC","time":1741683599768,'
        '"levels":[[{"px":"1","sz":"1","n":1}],[{"px":"2","sz":"1","n":1}]]}}'
    )
    records = list(iter_l2_records(raw.encode("utf-8")))
    assert len(records) == 1
    assert records[0]["coin"] == "BTC"


def test_assert_spot_coin_blocked():
    import pytest
    from analytics.lib.s3_orderbook import assert_s3_orderbook_coin_supported

    with pytest.raises(RuntimeError, match="Spot-Coin"):
        assert_s3_orderbook_coin_supported("@142")


def test_assert_hip3_coin_blocked():
    import pytest
    from analytics.lib.s3_orderbook import assert_s3_orderbook_coin_supported

    with pytest.raises(RuntimeError, match="HIP-3"):
        assert_s3_orderbook_coin_supported("xyz:MU")


def test_plan_hour_downloads_splits_missing(monkeypatch):
    from analytics.lib.s3_orderbook import HourKey, plan_hour_downloads

    class FakeClient:
        def head_object(self, **kwargs):
            key = kwargs["Key"]
            if "20250901" in key:
                return {"ContentLength": 1000}
            raise type("E", (Exception,), {"response": {"Error": {"Code": "404"}}})()

    hours = [HourKey("20250901", 10), HourKey("20251021", 10)]
    plan = plan_hour_downloads(FakeClient(), "BTC", hours)
    assert len(plan.available) == 1
    assert len(plan.missing) == 1
    assert plan.estimated_bytes() == 1000


def test_normalize_metrics_timestamps_strips_utc():
    from analytics.lib.s3_orderbook import normalize_metrics_timestamps

    ts = datetime(2025, 3, 11, 9, 29, 0, tzinfo=timezone.utc)
    df = pl.DataFrame({"snapshot_timestamp": [ts], "spread": [0.5]})
    out = normalize_metrics_timestamps(df)
    assert out.schema["snapshot_timestamp"] == pl.Datetime("ms")
    assert str(out["snapshot_timestamp"][0]).startswith("2025-03-11")


def test_snapshots_for_fills_asof_backward():
    fill_ts = datetime(2025, 3, 11, 9, 30, 0, tzinfo=timezone.utc)
    fills = pl.DataFrame(
        {
            "timestamp": [fill_ts],
            "coin": ["BTC"],
            "hash": ["abc"],
        }
    )
    metrics = pl.DataFrame(
        {
            "snapshot_timestamp": [
                datetime(2025, 3, 11, 9, 29, 0, tzinfo=timezone.utc),
                datetime(2025, 3, 11, 9, 31, 0, tzinfo=timezone.utc),
            ],
            "best_bid_px": [100.0, 101.0],
            "best_bid_sz": [1.0, 1.0],
            "best_ask_px": [100.5, 101.5],
            "best_ask_sz": [1.0, 1.0],
            "spread": [0.5, 0.5],
            "mid_px": [100.25, 101.25],
            "bid_depth_top5": [1.0, 1.0],
            "ask_depth_top5": [1.0, 1.0],
            "bid_levels_json": [json.dumps([{"px": 100.0, "sz": 1.0, "n": 1}]), None],
            "ask_levels_json": [json.dumps([{"px": 100.5, "sz": 1.0, "n": 1}]), None],
        }
    )
    out = snapshots_for_fills(fills, "BTC", {("20250311", 9): metrics})
    assert len(out) == 1
    assert out["best_bid_px"][0] == 100.0
