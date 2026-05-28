"""
Force pull all historical trades for a Hyperliquid wallet.

Writes a legacy flat Parquet file. For the full dashboard bundle (orders,
funding, ledger, market data), use force_pull_wallet_bundle.py instead.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SCRIPT = REPO_ROOT / "analytics" / "scripts" / "force_pull_wallet_bundle.py"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Force pull wallet (delegates to bundle puller)."
    )
    parser.add_argument("wallet", help="The 0x... wallet address to pull")
    parser.add_argument(
        "--legacy-only",
        action="store_true",
        help="Write only flat fills.parquet (old behaviour)",
    )
    args = parser.parse_args()

    wallet = args.wallet.strip()
    if not wallet.startswith("0x"):
        print("Error: Wallet address must start with 0x")
        return 1

    if args.legacy_only:
        sys.path.insert(0, str(REPO_ROOT))
        from analytics.lib import hl_fetch
        from analytics.lib.parquet_io import atomic_write_parquet

        fills = hl_fetch.fetch_fills_all(wallet)
        df = hl_fetch.fills_to_dataframe(fills)
        out_dir = REPO_ROOT / "analytics" / "data_lake" / "wallets"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{wallet.lower()}.parquet"
        atomic_write_parquet(df, path, compression="zstd")
        print(f"Saved {len(df)} fills to {path}")
        return 0

    print("Running full wallet bundle pull ...")
    return subprocess.call(
        [sys.executable, str(BUNDLE_SCRIPT), wallet],
        cwd=str(REPO_ROOT),
    )


if __name__ == "__main__":
    raise SystemExit(main())
