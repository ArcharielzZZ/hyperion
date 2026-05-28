# Local data lake (not in git)

Wallet bundles and Twitter pulls from the Wallet Explorer live here after force-pull.

- `wallets/<0x...>/` — fills, orders, market parquet, `meta.json`
- `twitter/` — tweet parquet, pull status, optional Chrome profile for X login

Regenerate locally:

```powershell
python analytics/scripts/force_pull_wallet_bundle.py 0xYourWallet...
python analytics/scripts/force_pull_twitter.py --handle user --from 2025-01-01 --to 2026-12-31
```

See `docs/DATA_ARTIFACTS.md`.
