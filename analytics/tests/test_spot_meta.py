"""Tests for Hyperliquid spot coin display resolution."""

from __future__ import annotations

from analytics.lib import spot_meta


def test_build_index_names_from_fixture():
    meta = {
        "tokens": [
            {"index": 0, "name": "USDC"},
            {"index": 197, "name": "UBTC"},
        ],
        "universe": [
            {"index": 142, "tokens": [197, 0], "name": "@142", "isCanonical": False},
        ],
    }
    assert spot_meta._build_index_names(meta) == {142: "UBTC/USDC"}


def test_resolve_coin_display_perp_unchanged():
    assert spot_meta.resolve_coin_display("BTC") == "BTC"
    assert spot_meta.resolve_coin_display("ETH") == "ETH"


def test_is_spot_coin():
    assert spot_meta.is_spot_coin("@142") is True
    assert spot_meta.is_spot_coin("BTC") is False


def test_resolve_coin_display_spot(monkeypatch):
    monkeypatch.setattr(
        spot_meta,
        "spot_index_names",
        lambda **_: {142: "UBTC/USDC"},
    )
    assert spot_meta.resolve_coin_display("@142") == "UBTC/USDC"


def test_format_coin_option_label_spot(monkeypatch):
    monkeypatch.setattr(
        spot_meta,
        "resolve_coin_display",
        lambda coin: "UBTC/USDC" if coin == "@142" else coin,
    )
    assert spot_meta.format_coin_option_label("@142", 120) == "UBTC/USDC (120 fills)"
    assert spot_meta.format_coin_option_label("BTC", 5000) == "BTC (5,000 fills)"


def test_format_coin_chart_title_includes_api_id(monkeypatch):
    monkeypatch.setattr(
        spot_meta,
        "resolve_coin_display",
        lambda coin: "UBTC/USDC" if coin == "@142" else coin,
    )
    assert spot_meta.format_coin_chart_title("@142") == "UBTC/USDC (@142)"
    assert spot_meta.format_coin_chart_title("BTC") == "BTC"
