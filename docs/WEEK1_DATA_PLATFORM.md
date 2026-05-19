# Week 1 — Data platform (definition of done)

This document is the operational contract for the **Week 1 quant roadmap** slice: ingestion auditability, scanner observability without ad hoc SQL, daily discovery cohort versioning, storage scan efficiency, and a repeatable export path.

## Definition of done (Week 1)

| Theme | Deliverable | Status |
| --- | --- | --- |
| Ingestion reliability | Append-only `ingest_connection_events` populated from the Hyperliquid websocket loop (connect, subscribe failures, ping/pong, timeouts, remote close, session cycle summary). | Implemented in `hyperion-ingest` |
| Scanner observability | Live freshness JSON via API; historical rows in `scanner_coverage_snapshots`; optional capture via API POST or scheduled SQL script. | Implemented |
| Tracked universe versioning | `discovery_universe_daily` keyed by `(snapshot_date, trader_id)` with one row per ranked trader for that UTC date. | Implemented + script |
| Storage hardening | BRIN indexes on large time-series tables (`fills`, `trade_ticks`, `market_snapshots`); retention/partition strategy documented below. | BRIN + plan |
| Nightly export | `pg_dump` of core tables to a timestamped custom-format file under `exports/week1/`. | Script |

## Schema (migration `0010_week1_data_platform.sql`)

### `ingest_connection_events`

- **Purpose:** Reconnect and lifecycle audit for gap analysis and SLO discussions.
- **Columns:** `occurred_at`, `event_type`, `detail` (JSONB).
- **Typical `event_type` values:** `ws_connected`, `ws_subscribe_initial_failed`, `ws_ping_failed`, `ws_subscribe_discovery_failed`, `ws_pong_reply_failed`, `ws_remote_close`, `ws_stream_error`, `ws_stream_ended`, `ws_receive_timeout`, `ws_session_cycle_complete`, `ws_connect_failed`, `ws_shutdown_requested`, etc.

### `discovery_universe_daily`

- **Purpose:** Immutable daily cohort of `trader_discovery_rankings` for reproducible research (as-of date).
- **Primary key:** `(snapshot_date, trader_id)`.

### `scanner_coverage_snapshots`

- **Purpose:** Point-in-time JSON payloads (same shape as the live freshness view) for dashboards and incident timelines.

### `v_data_freshness_now`

- **Purpose:** Single-row view with counts, max timestamps, and wall-clock lag (seconds) for hot tables plus `discovery_promoted` count.
- **Usage:** Read-only; cheap `SELECT` for APIs and health checks.

### BRIN indexes

- `brin_fills_timestamp`, `brin_trade_ticks_timestamp`, `brin_market_snapshots_timestamp` — reduce sequential scan cost on time-ranged analytics as row counts grow.

## API (internal operators)

Bind the API to localhost in production if these routes are unauthenticated.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/internal/data/freshness` | Current row from `v_data_freshness_now` as JSON. |
| `GET` | `/internal/scanner/coverage/snapshots?limit=48` | Newest stored snapshots (`id`, `captured_at`, `payload`). |
| `POST` | `/internal/scanner/coverage/snapshots` | Inserts one snapshot from `v_data_freshness_now`; returns `{ "id": <bigint> }`. |
| `GET` | `/internal/ingest/connection-events?limit=100` | Newest ingest lifecycle events. |

## Scheduled jobs (recommended cadence)

| Job | Schedule | Mechanism |
| --- | --- | --- |
| Discovery universe snapshot | Daily, after trader discovery rankings refresh (UTC boundary) | `infra/scripts/discovery-universe-daily.ps1` |
| Scanner coverage snapshot | Hourly or every 15 minutes | `infra/scripts/scanner-coverage-snapshot.ps1` or `POST /internal/scanner/coverage/snapshots` |
| Core data export | Nightly | `infra/scripts/week1-nightly-export.ps1` |

Scripts load `DATABASE_URL` from `.env` (or `.env.example`). They prefer `psql` / `pg_dump` on the host; if missing, they use `docker exec` against `hyperion-postgres` when that container is running.

### Makefile shortcuts (from `hyperion/`)

- `make week1-discovery-snapshot`
- `make week1-scanner-snapshot`
- `make week1-nightly-export`

## Retention and partitioning (plan)

**Current:** single PostgreSQL instance; time-series tables grow monotonically.

**Near-term (Week 2+):**

1. **Declarative retention:** document maximum useful history per table (e.g. raw `trade_ticks` vs aggregated bars). Enforce with scheduled `DELETE` or partition `DROP` after monthly partitions exist.
2. **Partitioning:** migrate `fills`, `trade_ticks`, and `market_snapshots` to monthly range partitions on `timestamp` once ingest volume crosses an agreed threshold (ingest rows/day or table size on disk).
3. **Archival:** nightly `week1-nightly-export.ps1` (or object-store upload of the same dump) is the cold-archive path until a dedicated object lake pipeline exists.

## Applying migrations

From repository root `hyperion/`:

```text
make migrate
```

Or: `powershell -ExecutionPolicy Bypass -File infra/scripts/migrate.ps1`

## Restore (from custom-format dump)

```text
pg_restore -d "$DATABASE_URL" --clean --if-exists path\to\week1_core_*.dump
```

Use `--section=pre-data` / `--section=data` as appropriate for partial restores in shared environments.
