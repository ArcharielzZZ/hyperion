# MONOLITH / Hyperion

Local-first crypto behavioral intelligence platform scaffold.

This repository is intentionally structured as a quant research and trader-intelligence system, not a retail copytrading bot. Phase 1 establishes the local operating foundation: reliable ingestion, normalized storage, scoring, signal generation, paper execution, and an API surface that can later evolve into a distributed multi-service platform.

## Workspace Layout

```text
hyperion/
├── services/
│   ├── api/
│   ├── execution-engine/
│   ├── ingest/
│   ├── signal-engine/
│   └── trader-engine/
├── shared/
│   ├── config/
│   ├── models/
│   ├── schemas/
│   └── utils/
├── infra/
│   ├── docker/
│   ├── migrations/
│   └── scripts/
├── analytics/
│   ├── backtests/
│   ├── notebooks/
│   └── research/
├── dashboard/
└── docs/
```

## Quick Start

**Repository policy:** application code and schema in git; heavy data (Postgres, Parquet, audit exports, build artifacts) in `.gitignore`. Team data bundles: [docs/DATA_ARTIFACTS.md](docs/DATA_ARTIFACTS.md).

1. Copy `.env.example` to `.env` and adjust values if needed.
2. Start local infra with `make dev-up`.
3. Apply schema with `make migrate`.
4. Run services in separate terminals:
   - `make run-ingest`
   - `make run-trader`
   - `make run-signal`
   - `make run-execution`
   - `make run-api`

## Local Ports

- `8080` API
- `8081` ingest health + metrics
- `8082` trader-engine
- `8083` signal-engine
- `8084` execution-engine
- `5432` PostgreSQL
- `5050` pgAdmin
- `6379` Redis

## Current Phase

The current implementation is a serious starter foundation:

- Rust cargo workspace with shared config, schemas, models, and utilities
- **Python pipeline** (`python/`) — historical Parquet lake, Polars analytics scaffold, pluggable ranking (see `python/docs/ARCHITECTURE.md`)
- Local Docker Compose for PostgreSQL, Redis, and pgAdmin
- PostgreSQL schema for normalized trader, fill, position, pnl, market, score, signal, and paper-trade data
- Hyperliquid websocket ingestion scaffold with reconnect logic and health endpoints
- Trader scoring, signal generation, and paper execution starter engines
- Axum API service for leaderboards, scores, signals, simulations, health, and metrics

See `docs/architecture.md` and `docs/local-development.md` for deeper details.

## Week 1 data platform (closed via ritual)

Observability, gap detection, cohort snapshots, and export tooling: `docs/WEEK1_DATA_PLATFORM.md`.

**Close Week 1:** `make week1-close` (requires live ingest + trader-engine, or `-AllowStoppedServices`).

**Week 2:** `docs/WEEK2_FEATURE_LAYER.md`.
