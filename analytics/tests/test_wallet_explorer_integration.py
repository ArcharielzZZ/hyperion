"""Integration smoke tests for Wallet Explorer load/score/chart paths."""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DASH_DIR = REPO_ROOT / "analytics" / "dashboard"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(DASH_DIR))

import wallet_pulls  # noqa: E402
import twitter_pulls  # noqa: E402
from callbacks_wallet import (  # noqa: E402
    _build_score_panel,
    coin_options,
    format_wallet_status_message,
    load_wallet,
)
from wallet_chart import (  # noqa: E402
    _candle_range_note,
    aggregate_order_placement_markers,
    aggregate_trades,
    build_figure,
    join_buckets_with_candles,
)
from wallet_explorer import app  # noqa: E402

BUNDLE_WALLET = "0x2d99fe0f36c1aebd28a1a2c0e82e8ca13c2ea351"
LEGACY_WALLET = "0x5f94a51948d2376ad34a6fadfa2544e651b74b96"


@pytest.fixture(scope="module")
def bundle_wallet() -> str:
    if not wallet_pulls.has_wallet_data(BUNDLE_WALLET):
        pytest.skip("Bundle wallet not in data_lake")
    return BUNDLE_WALLET


def test_wallet_explorer_app_imports():
    assert app.layout is not None
    assert len(app.callback_map) >= 10


def test_load_wallet_pipeline(bundle_wallet: str):
    wallet_pulls.clear_wallet_cache(bundle_wallet)
    df = load_wallet(bundle_wallet)
    opts = coin_options(df)
    assert len(df) > 0
    assert len(opts) > 0
    msg = format_wallet_status_message(bundle_wallet, df, len(opts))
    assert "OK" in msg
    panel = _build_score_panel(bundle_wallet, df)
    assert panel is not None


def test_chart_build_uses_twitter_pulls(bundle_wallet: str):
    wallet_pulls.clear_wallet_cache(bundle_wallet)
    df = load_wallet(bundle_wallet)
    coin = coin_options(df)[0]["value"]
    candles = wallet_pulls.load_candles(bundle_wallet, coin, "4h")
    if candles is None or candles.is_empty():
        pytest.skip(f"No cached candles for {coin}")

    trades = df.filter(pl.col("coin") == coin)
    orders = wallet_pulls.load_orders(bundle_wallet).filter(pl.col("coin") == coin)
    op = aggregate_order_placement_markers(orders, "4h")
    opj = join_buckets_with_candles(op, candles)
    buckets = aggregate_trades(df, coin, "4h")
    bj = join_buckets_with_candles(buckets, candles)

    fig = build_figure(
        coin,
        "4h",
        candles,
        bj,
        bundle_wallet,
        tweets_df=twitter_pulls.load_visible_tweets(),
        orders_df=orders,
        order_placement_joined=opj,
        funding_df=pl.DataFrame(),
        ledger_df=wallet_pulls.load_ledger(bundle_wallet),
        candle_note=_candle_range_note(candles, trades),
    )
    assert len(fig.data) > 0


def test_legacy_wallet_scores_without_candles():
    if not wallet_pulls.has_wallet_data(LEGACY_WALLET):
        pytest.skip("Legacy wallet not in data_lake")
    wallet_pulls.clear_wallet_cache(LEGACY_WALLET)
    df = load_wallet(LEGACY_WALLET)
    panel = _build_score_panel(LEGACY_WALLET, df)
    assert panel is not None
