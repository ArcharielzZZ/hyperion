import argparse
import requests
import polars as pl
import time
import os
from pathlib import Path
from datetime import datetime

# ==========================================
# HYPERION: Wallet Force Puller
# ==========================================
# This script downloads the complete historical trade history (fills)
# of a specific wallet from Hyperliquid and saves it as a compressed
# Parquet file in the data lake for immediate Polars analysis.

HYPERLIQUID_API_URL = "https://api.hyperliquid.xyz/info"
DATA_LAKE_DIR = Path("analytics/data_lake/wallets")

def fetch_wallet_fills(wallet_address: str) -> list:
    """
    Fetches all historical fills for a given wallet from Hyperliquid via
    `userFillsByTime`, paginating FORWARD in time.

    Hyperliquid returns fills oldest-first from `userFillsByTime` and caps
    each response at 2000 rows. We therefore advance `startTime = newest + 1`
    after each batch and stop when a batch comes back shorter than the cap.

    Hash-dedupe prevents accidental double-inserts when two fills share the
    same millisecond timestamp at a page boundary.
    """
    print(f"Starting force pull for wallet: {wallet_address}")

    BATCH_CAP = 2000
    headers = {"Content-Type": "application/json"}

    all_fills: list = []
    seen_hashes: set = set()

    cur_start = 0
    end_time = int(time.time() * 1000) + 60_000  # +1 min safety buffer

    while cur_start < end_time:
        print(f"Fetching from startTime={cur_start} ... (Current total: {len(all_fills)})")

        payload = {
            "type": "userFillsByTime",
            "user": wallet_address,
            "startTime": cur_start,
            "endTime": end_time,
        }
        response = requests.post(HYPERLIQUID_API_URL, json=payload, headers=headers)

        if response.status_code != 200:
            print(f"Error: API returned status code {response.status_code}")
            print(response.text)
            break

        batch = response.json() or []
        if not batch:
            print("No more fills found.")
            break

        fresh = [f for f in batch if f.get("hash") not in seen_hashes]
        for f in fresh:
            seen_hashes.add(f.get("hash"))
        all_fills.extend(fresh)

        newest_ts = max(f["time"] for f in batch)

        # Last page when Hyperliquid returns fewer rows than the cap
        if len(batch) < BATCH_CAP:
            print(f"  Last page (batch={len(batch)} < cap={BATCH_CAP}).")
            break

        # Defensive guard against infinite loops
        if newest_ts < cur_start:
            print(f"  Pagination guard hit: newest_ts {newest_ts} < cur_start {cur_start}.")
            break

        cur_start = newest_ts + 1
        time.sleep(0.3)

    return all_fills

def process_and_save(wallet_address: str, fills: list):
    """
    Converts the raw JSON fills into a highly optimized Polars DataFrame
    and saves it as a Parquet file.
    """
    if not fills:
        print("No data to save.")
        return
        
    print(f"Processing {len(fills)} fills into Polars DataFrame...")
    
    # Create DataFrame
    df = pl.DataFrame(fills)
    
    # Hyperliquid returns timestamps in milliseconds. Convert to proper datetime.
    # Also cast numeric strings to actual floats for analysis
    df = df.with_columns([
        pl.from_epoch(pl.col("time"), time_unit="ms").alias("timestamp"),
        pl.col("px").cast(pl.Float64).alias("price"),
        pl.col("sz").cast(pl.Float64).alias("size"),
        pl.col("fee").cast(pl.Float64).alias("fee"),
        pl.col("closedPnl").cast(pl.Float64).alias("closed_pnl")
    ])
    
    # Determine side based on 'dir' (direction)
    # Hyperliquid uses 'Open Long', 'Close Long', 'Open Short', 'Close Short'
    # We simplify this to just LONG or SHORT for basic analysis
    df = df.with_columns(
        pl.when(pl.col("dir").str.contains("Long"))
        .then(pl.lit("LONG"))
        .otherwise(pl.lit("SHORT"))
        .alias("side")
    )
    
    # Sort chronologically (oldest first) - CRITICAL for AS OF joins later!
    df = df.sort("timestamp")
    
    # Select only the columns we care about for research
    df_clean = df.select([
        "timestamp", 
        "coin", 
        "side", 
        "dir", 
        "price", 
        "size", 
        "fee", 
        "closed_pnl", 
        "hash"
    ])
    
    # Ensure directory exists
    DATA_LAKE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Save to Parquet
    file_path = DATA_LAKE_DIR / f"{wallet_address}.parquet"
    print(f"Saving to {file_path}...")
    
    # Use high compression for cold storage
    df_clean.write_parquet(file_path, compression="zstd")
    
    print(f"✅ Success! Saved {len(fills)} trades to {file_path}")
    print("\nData Preview:")
    print(df_clean.head(5))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Force pull all historical trades for a Hyperliquid wallet.")
    parser.add_argument("wallet", help="The 0x... wallet address to pull")
    
    args = parser.parse_args()
    
    wallet = args.wallet
    if not wallet.startswith("0x"):
        print("Error: Wallet address must start with 0x")
        exit(1)
        
    fills = fetch_wallet_fills(wallet)
    process_and_save(wallet, fills)
