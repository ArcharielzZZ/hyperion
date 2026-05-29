"""
Hyperion: S3 L2 Order Book Pull (minimal, quota-aware)
======================================================
Downloads only UTC hour files that contain fills for the given coin,
extracts one market snapshot per fill, and stores compact Parquet.

CLI
---
    python analytics/scripts/force_pull_orderbook.py 0xWallet... BTC
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from analytics.lib.s3_orderbook import pull_orderbook_for_wallet_coin  # noqa: E402

WALLETS_DIR = REPO_ROOT / "analytics" / "data_lake" / "wallets"


def write_status(wallet: str, **fields) -> None:
    path = WALLETS_DIR / wallet.lower() / ".orderbook_pull_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "wallet": wallet.lower(),
        **fields,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull minimal S3 L2 order book snapshots.")
    parser.add_argument("wallet", help="Wallet address (0x...)")
    parser.add_argument("coin", help="Coin symbol, e.g. BTC")
    args = parser.parse_args()

    wallet = args.wallet.strip().lower()
    coin = args.coin.strip()
    if not wallet.startswith("0x"):
        print("Error: wallet must start with 0x")
        return 2
    if not coin:
        print("Error: coin required")
        return 2

    write_status(
        wallet,
        state="running",
        coin=coin,
        message="Starte Order-Book-Pull ...",
    )

    def _status_cb(**fields) -> None:
        write_status(wallet, coin=coin, **fields)

    try:
        merged = pull_orderbook_for_wallet_coin(
            wallet,
            coin,
            status_callback=_status_cb,
        )
        write_status(
            wallet,
            state="done",
            coin=coin,
            message=f"Fertig — {len(merged)} Snapshots gespeichert.",
            snapshot_count=len(merged),
        )
        print(f"OK: {len(merged)} order book snapshots for {coin}")
        return 0
    except Exception as exc:
        write_status(
            wallet,
            state="error",
            coin=coin,
            message=str(exc),
            error=str(exc),
        )
        traceback.print_exc()
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
