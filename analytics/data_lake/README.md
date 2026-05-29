# Local data lake (not in git)

Wallet bundles, Twitter pulls, and S3 order book cache from the Wallet Explorer live here after force-pull.

- `wallets/<0x...>/` — fills, orders, market parquet, `meta.json`
- `wallets/<0x...>/market/<COIN>_orderbook_snapshots.parquet` — S3 L2 snapshots per fill
- `s3_cache/<COIN>/YYYYMMDD/<H>_metrics.parquet` — hour-level S3 cache (no re-download)
- `s3_cache/_baseline/liquidity_baseline.parquet` — spread_bps mu/sigma per (coin, utc_hour); built from cache only (0 GB S3)
- `s3_usage/<YYYY-MM>.json` — monthly S3 download counter (100 GB cap)
- `twitter/` — tweet parquet, pull status, optional Chrome profile for X login

Regenerate locally:

```powershell
python analytics/scripts/force_pull_wallet_bundle.py 0xYourWallet...
python analytics/scripts/force_pull_orderbook.py 0xYourWallet BTC
python analytics/scripts/build_liquidity_baseline.py
python analytics/scripts/force_pull_twitter.py --handle user --from 2025-01-01 --to 2026-12-31
```

See `docs/DATA_ARTIFACTS.md`.
