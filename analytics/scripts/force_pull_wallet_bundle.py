"""
Hyperion: Wallet Bundle Force Puller
====================================
Downloads full wallet history plus orders, user funding, ledger, and per-coin
market data (4h candles + funding rates) into a bundle directory for the
Wallet Explorer dashboard.

CLI
---
    python analytics/scripts/force_pull_wallet_bundle.py 0xYourWallet...
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from analytics.lib import hl_fetch  # noqa: E402
from analytics.lib.parquet_io import atomic_write_parquet  # noqa: E402
from analytics.lib.schema_validate import assert_dataframe_columns  # noqa: E402
from analytics.lib.schemas import (  # noqa: E402
    CANDLES_SCHEMA,
    FILLS_SCHEMA,
    FUNDING_HISTORY_SCHEMA,
    LEDGER_SCHEMA,
    ORDERS_SCHEMA,
    USER_FUNDING_SCHEMA,
)
from analytics.lib.coin_paths import market_candles_path, market_funding_path
from analytics.lib.spot_meta import format_coin_chart_title  # noqa: E402
from analytics.lib.time_utils import fills_time_bounds_ms  # noqa: E402

WALLETS_DIR = REPO_ROOT / "analytics" / "data_lake" / "wallets"
DEFAULT_CANDLE_INTERVAL = "4h"
MARKET_DATA_RETRIES = 5
MARKET_DATA_RETRY_BASE_SEC = 2.0


def _atomic_write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def write_status(bundle_dir: Path, **fields) -> None:
    path = bundle_dir / ".pull_status.json"
    base = {
        "state": "running",
        "wallet": "",
        "message": "",
        "error": "",
        "fill_count": 0,
        "coin_index": 0,
        "coin_total": 0,
        "current_coin": "",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing.pop("error", None)
            base.update(existing)
        except Exception:
            pass
    base.update(fields)
    if base.get("state") != "error":
        base["error"] = str(base.get("error") or "")
    base["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write_json(path, base)


def _call_with_retries(label: str, fn):
    last_exc: Exception | None = None
    for attempt in range(MARKET_DATA_RETRIES):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < MARKET_DATA_RETRIES - 1:
                delay = MARKET_DATA_RETRY_BASE_SEC * (2**attempt)
                print(f"    retry {attempt + 1}/{MARKET_DATA_RETRIES - 1} for {label} in {delay:.0f}s ...")
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _pull_coin_market_data(
    market_dir: Path,
    coin: str,
    c_start: int,
    c_end: int,
) -> dict[str, str] | None:
    """Fetch candles + funding; return failure dict or None on full success."""
    errors: dict[str, str] = {}

    try:
        candles = _call_with_retries(
            f"{coin} candles",
            lambda: hl_fetch.fetch_candles_chunked(
                coin, DEFAULT_CANDLE_INTERVAL, c_start, c_end
            ),
        )
        candles_df = hl_fetch.candles_to_dataframe(candles)
        assert_dataframe_columns(
            candles_df, CANDLES_SCHEMA, label=f"{coin} candles"
        )
        atomic_write_parquet(
            candles_df,
            market_candles_path(market_dir, coin, DEFAULT_CANDLE_INTERVAL),
            compression="zstd",
        )
    except Exception as exc:
        errors["candles"] = str(exc)

    try:
        funding_rows = _call_with_retries(
            f"{coin} funding",
            lambda: hl_fetch.fetch_funding_history(coin, c_start, c_end),
        )
        funding_df = hl_fetch.funding_history_to_dataframe(funding_rows)
        assert_dataframe_columns(
            funding_df, FUNDING_HISTORY_SCHEMA, label=f"{coin} funding"
        )
        atomic_write_parquet(
            funding_df,
            market_funding_path(market_dir, coin),
            compression="zstd",
        )
    except Exception as exc:
        errors["funding"] = str(exc)

    if not errors:
        return None

    parts = [f"{kind}: {msg}" for kind, msg in errors.items()]
    return {"coin": coin, "error": "; ".join(parts), **errors}


def pull_wallet_bundle(wallet: str) -> int:
    wallet_l = wallet.lower()
    if not wallet_l.startswith("0x"):
        print("Error: wallet must start with 0x")
        return 2

    bundle_dir = WALLETS_DIR / wallet_l
    market_dir = bundle_dir / "market"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    market_dir.mkdir(parents=True, exist_ok=True)

    write_status(
        bundle_dir,
        state="running",
        wallet=wallet_l,
        message="Lade Fills ...",
    )

    try:
        print(f"Pulling fills for {wallet_l} ...")
        raw_fills = hl_fetch.fetch_fills_all(wallet_l)
        fills_df = hl_fetch.fills_to_dataframe(raw_fills)
        assert_dataframe_columns(fills_df, FILLS_SCHEMA, label="fills")
        atomic_write_parquet(
            fills_df, bundle_dir / "fills.parquet", compression="zstd"
        )
        fill_count = len(fills_df)
        print(f"  -> {fill_count} fills")

        if fill_count == 0:
            write_status(
                bundle_dir,
                state="done",
                message="Keine Fills gefunden.",
                fill_count=0,
            )
            _atomic_write_json(
                bundle_dir / "meta.json",
                {
                    "wallet": wallet_l,
                    "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "fill_count": 0,
                    "coins": [],
                    "default_interval": DEFAULT_CANDLE_INTERVAL,
                },
            )
            return 0

        start_ms, end_ms = fills_time_bounds_ms(fills_df)

        write_status(bundle_dir, message="Lade Orders ...", fill_count=fill_count)
        print("Pulling historical orders (~2000 max) ...")
        orders_df = hl_fetch.orders_to_dataframe(
            hl_fetch.fetch_historical_orders(wallet_l)
        )
        assert_dataframe_columns(orders_df, ORDERS_SCHEMA, label="orders")
        atomic_write_parquet(
            orders_df, bundle_dir / "orders.parquet", compression="zstd"
        )
        print(f"  -> {len(orders_df)} orders")

        write_status(bundle_dir, message="Lade userFunding ...")
        print("Pulling user funding ...")
        uf_df = hl_fetch.user_funding_to_dataframe(
            hl_fetch.fetch_user_funding(wallet_l, start_ms, end_ms)
        )
        assert_dataframe_columns(uf_df, USER_FUNDING_SCHEMA, label="user_funding")
        atomic_write_parquet(
            uf_df, bundle_dir / "user_funding.parquet", compression="zstd"
        )
        print(f"  -> {len(uf_df)} user funding rows")

        write_status(bundle_dir, message="Lade Ledger ...")
        print("Pulling ledger updates ...")
        ledger_df = hl_fetch.ledger_to_dataframe(
            hl_fetch.fetch_ledger(wallet_l, start_ms, end_ms)
        )
        assert_dataframe_columns(ledger_df, LEDGER_SCHEMA, label="ledger")
        atomic_write_parquet(
            ledger_df, bundle_dir / "ledger.parquet", compression="zstd"
        )
        print(f"  -> {len(ledger_df)} ledger rows")

        coins = (
            fills_df.group_by("coin")
            .agg(pl.len().alias("n"))
            .sort("n", descending=True)["coin"]
            .to_list()
        )
        coin_total = len(coins)
        print(f"Pulling market data for {coin_total} coins ...")
        market_failures: list[dict[str, str]] = []

        for idx, coin in enumerate(coins, start=1):
            c_start, c_end = hl_fetch.coin_time_bounds_ms(fills_df, coin)
            coin_label = format_coin_chart_title(coin)
            write_status(
                bundle_dir,
                message=f"Marktdaten {idx}/{coin_total}: {coin_label}",
                coin_index=idx,
                coin_total=coin_total,
                current_coin=coin,
            )
            print(f"  [{idx}/{coin_total}] {coin_label} candles + funding ...")

            failure = _pull_coin_market_data(market_dir, coin, c_start, c_end)
            if failure:
                print(f"  WARNING: market data incomplete for {coin_label}: {failure['error']}")
                market_failures.append(failure)

        meta = {
            "wallet": wallet_l,
            "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "fill_count": fill_count,
            "coins": coins,
            "default_interval": DEFAULT_CANDLE_INTERVAL,
            "orders_note": "historicalOrders returns at most ~2000 recent orders",
        }
        if market_failures:
            meta["market_failures"] = market_failures

        _atomic_write_json(bundle_dir / "meta.json", meta)

        done_msg = f"Fertig - {fill_count:,} Fills, {coin_total} Coins."
        if market_failures:
            done_msg += (
                f" Marktdaten fuer {len(market_failures)} Coin(s) unvollstaendig"
                f" (Kerzen/Funding teilweise uebersprungen; Chart laedt live nach)."
            )

        write_status(
            bundle_dir,
            state="done",
            message=done_msg,
            error="",
            fill_count=fill_count,
            coin_index=coin_total,
            coin_total=coin_total,
            current_coin="",
        )
        print(f"Done. Bundle: {bundle_dir}")
        return 0

    except Exception as exc:
        write_status(
            bundle_dir,
            state="error",
            error=str(exc),
            message="Fehler beim Pull.",
        )
        traceback.print_exc()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force pull full wallet bundle for Wallet Explorer."
    )
    parser.add_argument("wallet", help="0x... wallet address")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    return pull_wallet_bundle(args.wallet.strip())


if __name__ == "__main__":
    raise SystemExit(main())
