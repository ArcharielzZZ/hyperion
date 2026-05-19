# Hyperion Python pipeline — architecture

## Goals

1. **Complement, not fork** the Rust monolith: reuse PostgreSQL, `event_key` dedupe, and existing `traders` / `fills` where possible.
2. **Scale historical volume** with Hive-style Parquet partitions for wallet scans and backtests.
3. **Decouple analytics** (Polars, rolling windows, incremental recomputation) from OLTP ingest latency.
4. **Configurable ranking** separate from Rust `scoring.rs` so research can iterate formulas without redeploying ingest.

## Layer diagram

```text
                    ┌─────────────────────┐
                    │  Hyperliquid REST   │
                    │  (historical fills) │
                    └──────────┬──────────┘
                               │
                    HistoricalBackfillService
                     (async workers, retries)
                               │
              ┌────────────────┴────────────────┐
              ▼                                 ▼
     FillNormalizer                    RateLimiter / Tenacity
              │
              ▼
     FillStorageEngine ─────► Parquet  /data/fills/year=…/month=…/day=…
              │
              └──────► PostgreSQL (ON CONFLICT event_key)  [planned]
                               ▲
                               │
     hyperion-ingest (Rust) ────┘  live WebSocket path

     UnifiedMergePipeline: ordering + dedupe policy shared contract
```

## Merge contract (live + historical)

- Every fill row carries a stable **`event_key`** (Rust migration `0003` defines uniqueness on `fills.event_key`).
- Python backfill **must** compute the same key formula before insert, or inserts will conflict by design (idempotent).
- **Ordering:** `event_ts` (exchange) vs `ingested_at` (`created_at`) — analytics use `event_ts`; operational lag uses `created_at`.

## Tables

| Table | Owner | Purpose |
| --- | --- | --- |
| `traders`, `fills`, … | Rust + shared | OLTP truth |
| `wallet_sync_state` | Python | per-wallet cursor / status |
| `backfill_checkpoints` | Python | resume granularity |
| `pipeline_wallet_metrics` | Python | JSONB metric blobs per window |
| `pipeline_ranking_snapshots` | Python | versioned leaderboard dumps |
| `pipeline_funding_payments` | Python | funding ledger (not in Rust yet) |

## Ranking engine (modular)

- **FactorRegistry:** register callables / named factors (Sharpe, win rate, …) with metadata.
- **WalletScoreCalculator:** pulls raw factor values, applies normalization + decay weights from YAML.
- **RankingEngine:** orchestrates batch runs, persists `pipeline_ranking_snapshots`.

Rust `scoring.rs` remains the **production default** for live services until this engine is promoted; Python engine supports **research parity** and **A/B configs**.

## Performance

- Polars **LazyFrame** + scan_parquet with partition filters (`year`, `month`, `day`).
- Backfill workers bounded by `asyncio.Semaphore`; chunk size from settings.
- Never `collect()` full history in one frame — windowed scans only.

## Future extensions (hooks)

- **ML:** feature store reads from Parquet + `pipeline_wallet_metrics`.
- **ClickHouse:** columnar mirror fed from Parquet or CDC; swap `FillStorageEngine` sink.
- **REST / dashboard:** read `pipeline_ranking_snapshots` + materialized views.

## Dependency injection

`hyperion_pipeline.container.Container` wires `Settings`, engines, and DB session factory for tests (override with fakes).
