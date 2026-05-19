# Hyperion Python pipeline

Async historical backfill, Parquet partitioning, Polars analytics, and a **pluggable ranking engine** that sits alongside the existing **Rust** services (`hyperion-ingest`, `hyperion-trader-engine`, etc.).

## What already exists (Rust)

| Area | Location |
| --- | --- |
| Live WebSocket ingest | `services/ingest/` |
| Trader scoring (percentile engine) | `services/trader-engine/src/scoring.rs` |
| PostgreSQL schema | `infra/migrations/*.sql` |
| Dedupe keys on fills | `fills.event_key` (unique) — **merge target** for Python backfill |
| Liquidations table | `liquidation_events` |

## What this package adds

- **Historical backfill** scaffolding with checkpoints, chunking, rate-limit hooks, and Parquet writes under `PIPELINE_DATA_DIR`.
- **SQLAlchemy + Pydantic** models for pipeline-owned tables (`0009_python_pipeline_tables.sql`).
- **Polars-first** analytics layout (lazy frames, rolling windows) — full metric catalog is staged with incremental extension points.
- **RankingEngine / FactorRegistry / WalletScoreCalculator** driven by YAML config (see `example_configs/`).
- **Unified merge** design: same `event_key` contract as Rust for idempotent inserts.
- **Typer CLI** via `hyperion-pipeline` or `python main.py`.

## Setup

```bash
cd hyperion/python
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -e ".[dev]"
```

Apply DB migration from repo root (same as Rust workflow):

```powershell
cd ..\..
powershell -ExecutionPolicy Bypass -File infra/scripts/migrate.ps1
```

Copy `../.env` or set `DATABASE_URL` / `PIPELINE_DATA_DIR` — see `src/hyperion_pipeline/config/settings.py`.

After backfilling fills, **scores do not refresh by themselves**. Run the Rust trader engine once so it can read the new rows:

`cargo run -p hyperion-trader-engine`

## Commands

```bash
hyperion-pipeline --help
# Latest up to 2000 fills from Hyperliquid → Postgres + Parquet (needs DATABASE_URL + migration 0009)
hyperion-pipeline backfill-wallet 0xYourWallet... --to-postgres --parquet
# Offline test from the Rust fixture file
hyperion-pipeline backfill-wallet 0x31ca8395cf837de08b24da3f660e77761dfb974b --fixture ..\services\ingest\fixtures\user_fills_snapshot.json --no-postgres
# Deeper window (uses userFillsByTime; may recurse if a slice returns 2000 rows)
hyperion-pipeline backfill-wallet 0xYourWallet... --start-ms 1700000000000 --end-ms 1730000000000
hyperion-pipeline rank-wallets
hyperion-pipeline export-leaderboard
```

## Docs

- `docs/ARCHITECTURE.md` — boundaries, merge rules, future ClickHouse / ML hooks.
