"""Resolve Hyperliquid spot coin symbols (``@142``) to human-readable pair names."""

from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any

import requests

from analytics.lib.hl_fetch import HL_API, HL_HEADERS, HL_TIMEOUT

_SPOT_COIN_RE = re.compile(r"^@(\d+)$")
_SPOT_META_TTL_SEC = 3600


def is_spot_coin(coin: str) -> bool:
    """True for Hyperliquid spot pair ids (``@142``, …)."""
    return bool(_SPOT_COIN_RE.match((coin or "").strip()))


def _build_index_names(meta: dict[str, Any]) -> dict[int, str]:
    tokens = {int(t["index"]): str(t["name"]) for t in meta.get("tokens", [])}
    out: dict[int, str] = {}
    for pair in meta.get("universe", []):
        idx = pair.get("index")
        if idx is None:
            continue
        index = int(idx)
        token_idxs = pair.get("tokens") or []
        if len(token_idxs) >= 2:
            base = tokens.get(int(token_idxs[0]), "?")
            quote = tokens.get(int(token_idxs[1]), "?")
            out[index] = f"{base}/{quote}"
        else:
            out[index] = str(pair.get("name") or f"@{index}")
    return out


@lru_cache(maxsize=1)
def _cached_spot_meta() -> tuple[dict[int, str], float]:
    """Return ``(index -> display name, fetched_at_unix)``."""
    r = requests.post(
        HL_API,
        json={"type": "spotMeta"},
        headers=HL_HEADERS,
        timeout=HL_TIMEOUT,
    )
    r.raise_for_status()
    return _build_index_names(r.json()), time.time()


def spot_index_names(*, force_refresh: bool = False) -> dict[int, str]:
    """Map spot pair index (e.g. 142) to display name (e.g. ``UBTC/USDC``)."""
    if force_refresh:
        _cached_spot_meta.cache_clear()
    names, fetched_at = _cached_spot_meta()
    if time.time() - fetched_at > _SPOT_META_TTL_SEC:
        _cached_spot_meta.cache_clear()
        names, _ = _cached_spot_meta()
    return names


def resolve_coin_display(coin: str) -> str:
    """Return readable label for API coin id; perps unchanged, ``@N`` -> ``BASE/QUOTE``."""
    match = _SPOT_COIN_RE.match(coin.strip())
    if not match:
        return coin
    index = int(match.group(1))
    try:
        return spot_index_names().get(index, coin)
    except Exception:
        return coin


def format_coin_option_label(coin: str, fill_count: int) -> str:
    """Dropdown label: resolved spot name + fill count."""
    display = resolve_coin_display(coin)
    if display != coin:
        return f"{display} ({fill_count:,} fills)"
    return f"{coin} ({fill_count:,} fills)"


def format_coin_chart_title(coin: str) -> str:
    """Chart title fragment; keeps ``@N`` hint when resolved."""
    display = resolve_coin_display(coin)
    if display != coin:
        return f"{display} ({coin})"
    return coin
