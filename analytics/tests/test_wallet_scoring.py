"""Tests for offline bundle wallet scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import polars as pl

from analytics.lib.wallet_scoring import (
    classify_style,
    compute_bundle_metrics,
    score_bundle_wallet,
    score_wallet_from_fills,
)
from analytics.dashboard.wallet_score_panel import render_score_panel


def _fill_row(
    ts: datetime,
    *,
    coin: str = "BTC",
    side: str = "buy",
    dir_: str = "Open Long",
    price: float = 100.0,
    size: float = 1.0,
    closed_pnl: float = 0.0,
) -> dict:
    return {
        "timestamp": ts,
        "coin": coin,
        "side": side,
        "dir": dir_,
        "price": price,
        "size": size,
        "fee": 0.1,
        "closed_pnl": closed_pnl,
        "hash": f"h-{ts.timestamp()}-{dir_}",
    }


def _scalp_fills(days: int = 10) -> pl.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for d in range(days):
        day = start + timedelta(days=d)
        for i in range(8):
            open_ts = day + timedelta(hours=i * 2)
            close_ts = open_ts + timedelta(minutes=20)
            rows.append(_fill_row(open_ts, dir_="Open Long", side="buy"))
            rows.append(
                _fill_row(
                    close_ts,
                    dir_="Close Long",
                    side="sell",
                    closed_pnl=5.0,
                )
            )
    return pl.DataFrame(rows)


def _swing_fills(days: int = 20) -> pl.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for d in range(days):
        day = start + timedelta(days=d)
        open_ts = day + timedelta(hours=1)
        close_ts = day + timedelta(hours=30)
        rows.append(_fill_row(open_ts, dir_="Open Long", side="buy"))
        rows.append(
            _fill_row(
                close_ts,
                dir_="Close Long",
                side="sell",
                closed_pnl=20.0,
            )
        )
    return pl.DataFrame(rows)


def _losing_fills(days: int = 10) -> pl.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows: list[dict] = []
    for d in range(days):
        day = start + timedelta(days=d)
        open_ts = day + timedelta(hours=1)
        close_ts = day + timedelta(hours=2)
        rows.append(_fill_row(open_ts, dir_="Open Long", side="buy"))
        rows.append(
            _fill_row(
                close_ts,
                dir_="Close Long",
                side="sell",
                closed_pnl=-50.0,
            )
        )
    return pl.DataFrame(rows)


def test_empty_fills_returns_none():
    assert score_wallet_from_fills(pl.DataFrame()) is None


def test_single_day_returns_skip_reason():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = pl.DataFrame([
        _fill_row(start, dir_="Open Long"),
        _fill_row(start + timedelta(hours=1), dir_="Close Long", closed_pnl=1.0),
    ])
    result = score_wallet_from_fills(fills)
    assert result is not None
    assert result.skip_reason is not None


def test_scalp_vs_swing_style_difference():
    scalp_metrics = compute_bundle_metrics(_scalp_fills())
    swing_metrics = compute_bundle_metrics(_swing_fills())
    assert scalp_metrics is not None
    assert swing_metrics is not None
    scalp_style, _ = classify_style(scalp_metrics)
    swing_style, _ = classify_style(swing_metrics)
    assert scalp_metrics.avg_holding_hours < swing_metrics.avg_holding_hours
    assert scalp_style in {"scalp", "unknown", "momentum"}
    assert swing_style in {"swing", "unknown", "mean_reversion"}


def test_positive_history_scores_higher_than_losing():
    win_metrics = compute_bundle_metrics(_swing_fills())
    lose_metrics = compute_bundle_metrics(_losing_fills())
    assert win_metrics is not None and lose_metrics is not None
    win = score_bundle_wallet(win_metrics)
    lose = score_bundle_wallet(lose_metrics)
    assert win.total_score > lose.total_score


def test_entry_timing_with_naive_candle_timestamps():
    """Regression: naive fill/candle datetimes must not mix with UTC-aware literals."""
    base = datetime(2025, 9, 24, 16, 0, 0)
    fills = pl.DataFrame([
        {
            "timestamp": base.replace(minute=11, second=48, microsecond=11000),
            "coin": "BTC",
            "side": "buy",
            "dir": "Open Long",
            "price": 100.0,
            "size": 1.0,
            "fee": 0.0,
            "closed_pnl": 0.0,
            "hash": "h1",
        },
        {
            "timestamp": base.replace(hour=18),
            "coin": "BTC",
            "side": "sell",
            "dir": "Close Long",
            "price": 101.0,
            "size": 1.0,
            "fee": 0.0,
            "closed_pnl": 1.0,
            "hash": "h2",
        },
    ])
    candles = pl.DataFrame({
        "timestamp": [
            base.replace(hour=16),
            base.replace(hour=20),
        ],
        "open": [99.0, 100.5],
        "high": [101.0, 102.0],
        "low": [98.0, 100.0],
        "close": [100.5, 101.5],
        "volume": [1.0, 1.0],
    })
    result = score_wallet_from_fills(fills, candles_by_coin={"BTC": candles})
    assert result is not None
    assert result.skip_reason is not None or result.total_score >= 0.0


def test_render_score_panel_smoke():
    metrics = compute_bundle_metrics(_swing_fills())
    assert metrics is not None
    result = score_bundle_wallet(metrics)
    panel = render_score_panel(result, {
        "fill_count": 40,
        "time_min": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "time_max": datetime(2026, 1, 20, tzinfo=timezone.utc),
        "pulled_at": "2026-05-25T12:00:00+00:00",
    })
    assert panel is not None
    assert result.style in {"swing", "unknown", "momentum", "scalp", "mean_reversion"}
