"""Safe filesystem names for Hyperliquid coin symbols (e.g. ``xyz:GOLD``)."""

from __future__ import annotations

from pathlib import Path

# Windows disallows ``:`` in file names; HL HIP-3 coins use ``prefix:SYMBOL``.
_WIN_INVALID_FILENAME_CHARS = '<>:"/\\|?*'


def sanitize_coin_for_filename(coin: str) -> str:
    """Map API coin symbol to a cross-platform filename fragment."""
    safe = coin.replace(":", "__")
    for ch in _WIN_INVALID_FILENAME_CHARS:
        if ch == ":":
            continue
        safe = safe.replace(ch, "_")
    return safe


def market_candles_filename(coin: str, interval: str) -> str:
    return f"{sanitize_coin_for_filename(coin)}_candles_{interval}.parquet"


def market_funding_filename(coin: str) -> str:
    return f"{sanitize_coin_for_filename(coin)}_funding.parquet"


def market_candles_path(market_dir: Path, coin: str, interval: str) -> Path:
    return market_dir / market_candles_filename(coin, interval)


def market_funding_path(market_dir: Path, coin: str) -> Path:
    return market_dir / market_funding_filename(coin)
