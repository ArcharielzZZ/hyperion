"""Tests for wallet store helpers and status formatting."""

from __future__ import annotations

from callbacks_wallet import (
    _format_pulled_at,
    format_wallet_status_message,
    pack_wallet_store,
    wallet_from_store,
)
import polars as pl


def test_format_pulled_at_iso():
    assert _format_pulled_at("2026-05-25T14:32:01") == "2026-05-25 14:32:01 UTC"


def test_format_pulled_at_empty():
    assert _format_pulled_at("") == ""


def test_wallet_store_roundtrip():
    store_a = pack_wallet_store("0xAbC")
    store_b = pack_wallet_store("0xAbC")
    assert store_a["wallet"] == "0xabc"
    assert store_b["rev"] != store_a["rev"]
    assert wallet_from_store(store_a) == "0xabc"
    assert wallet_from_store("0xlegacy") == "0xlegacy"


def test_format_wallet_status_shows_bundle_stand(monkeypatch):
    def fake_stats(_wallet, _fills):
        return {
            "fill_count": 100,
            "coin_count": 3,
            "pulled_at": "2026-05-25T14:32:01",
            "is_bundle": True,
            "orders_note": "",
            "order_count": 50,
            "time_min": "2024-01-01",
            "time_max": "2026-05-01",
            "market_failures": [],
        }

    monkeypatch.setattr("callbacks_wallet.wallet_pulls.bundle_stats", fake_stats)
    fills = pl.DataFrame(schema={"timestamp": pl.Datetime("ms"), "coin": pl.String})
    msg = format_wallet_status_message("0xabc", fills, 3)
    assert "Bundle-Stand: 2026-05-25 14:32:01 UTC" in msg
    assert "CLI-Pull" in msg


def test_format_wallet_status_shows_market_failures(monkeypatch):
    def fake_stats(_wallet, _fills):
        return {
            "fill_count": 100,
            "coin_count": 3,
            "pulled_at": "2026-05-25T14:32:01",
            "is_bundle": True,
            "orders_note": "",
            "order_count": 50,
            "time_min": "2024-01-01",
            "time_max": "2026-05-01",
            "market_failures": [{"coin": "@142", "error": "500"}],
        }

    monkeypatch.setattr("callbacks_wallet.wallet_pulls.bundle_stats", fake_stats)
    monkeypatch.setattr(
        "callbacks_wallet.format_coin_chart_title",
        lambda coin: "UBTC/USDC (@142)" if coin == "@142" else coin,
    )
    fills = pl.DataFrame(schema={"timestamp": pl.Datetime("ms"), "coin": pl.String})
    msg = format_wallet_status_message("0xabc", fills, 3)
    assert "Marktdaten unvollstaendig" in msg
    assert "UBTC/USDC (@142)" in msg
