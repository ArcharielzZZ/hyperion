# Week 1 — Data platform (definition of done)

**Status: CLOSED** when `week1-close.ps1` exits 0 (or operational checklist below is green).

Operational contract for the **Week 1 quant roadmap** slice: ingestion auditability, scanner observability without ad hoc SQL, daily discovery cohort versioning, storage scan efficiency, gap detection, and a repeatable export path.

## Definition of done (Week 1)

| Theme | Deliverable | Status |
| --- | --- | --- |
| Ingestion reliability | Append-only `ingest_connection_events` from the Hyperliquid websocket loop; gap views on reconnect downtime. | **Done** |
| Gap detector | `v_week1_ingest_downtime_gaps`, `v_week1_data_channel_health`, `v_week1_health_summary`; `week1-gap-report.ps1`. | **Done** (`0015`) |
| Scanner observability | Live freshness JSON; historical `scanner_coverage_snapshots`; internal API. | **Done** |
| Tracked universe versioning | `discovery_universe_daily` + daily script. | **Done** |
| Storage hardening | BRIN indexes; retention via ingest maintenance; partitioning plan (Week 2+ enforce). | **Done** (plan documented) |
| Nightly export | `pg_dump` of core tables under `exports/week1/`. | **Done** (script) |
| Closure ritual | Single command runs migrate → snapshots → gap report → export. | **Done** (`week1-close.ps1`) |
| Scheduled ops | Windows Task Scheduler installer for snapshots / export / gap report. | **Done** (optional install) |

## Close Week 1 (one command)

From `hyperion/` with Postgres up and live scoop running:

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/week1-close.ps1
```

Options:

| Flag | Use when |
| --- | --- |
| `-AllowStoppedServices` | DB checks only; ingest/trader not running |
| `-SkipExport` | Skip `pg_dump` (faster iteration) |
| `-SkipDiscoverySnapshot` | Skip daily cohort insert |
| `-InstallScheduledTasks` | Register Task Scheduler jobs after pass |

Makefile: `make week1-close`

### Install scheduled jobs (recommended for multi-week runs)

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/week1-install-scheduled-tasks.ps1
# Remove: ... week1-install-scheduled-tasks.ps1 -Unregister
```

| Task | Cadence |
| --- | --- |
| `Hyperion-Week1-ScannerSnapshot` | Every 15 minutes |
| `Hyperion-Week1-DiscoverySnapshot` | Daily ~00:15 UTC |
| `Hyperion-Week1-NightlyExport` | Daily 02:00 local |
| `Hyperion-Week1-GapReport` | Hourly |

## Schema

### Migration `0010_week1_data_platform.sql`

- `ingest_connection_events` — websocket lifecycle audit
- `discovery_universe_daily` — immutable daily discovery cohort
- `scanner_coverage_snapshots` — point-in-time freshness JSON
- `v_data_freshness_now` — single-row live lag view

### Migration `0015_week1_gap_detector.sql`

- `v_week1_data_channel_health` — per-table status (ingest: 300s/900s; `trader_scores`: 3900s/7200s)
- `v_week1_ingest_sessions` — connect → session end pairs
- `v_week1_ingest_downtime_gaps` — reconnect gaps > 60s
- `v_week1_health_summary` — rollup for APIs and close script

## API (internal operators)

Bind the API to localhost if unauthenticated.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/internal/data/freshness` | `v_data_freshness_now` as JSON |
| `GET` | `/internal/data/week1-health` | Summary + per-channel health (Week 1 closure) |
| `GET` | `/internal/scanner/coverage/snapshots?limit=48` | Historical scanner snapshots |
| `POST` | `/internal/scanner/coverage/snapshots` | Insert snapshot from live view |
| `GET` | `/internal/ingest/connection-events?limit=100` | Ingest lifecycle events |

## Scripts

| Script | Purpose |
| --- | --- |
| `discovery-universe-daily.ps1` | UTC cohort snapshot |
| `scanner-coverage-snapshot.ps1` | Insert freshness row |
| `week1-nightly-export.ps1` | `pg_dump` core tables |
| `week1-gap-report.ps1` | Markdown report under `exports/week1/reports/` |
| `week1-close.ps1` | Full closure ritual |
| `week1-install-scheduled-tasks.ps1` | Windows scheduled tasks |

Makefile: `week1-discovery-snapshot`, `week1-scanner-snapshot`, `week1-nightly-export`, `week1-gap-report`, `week1-close`, `week1-install-tasks`.

## Retention and partitioning (plan)

**Current:** single PostgreSQL instance; ingest runs `run_storage_maintenance` on `STORAGE_*_RETENTION_DAYS`.

**Week 2+:** monthly partitions and automated partition drop when volume threshold is hit (see canvas Week 2).

## Applying migrations

```text
make migrate
```

## Restore (from custom-format dump)

```text
pg_restore -d "$DATABASE_URL" --clean --if-exists path\to\week1_core_*.dump
```

## Success gate (canvas)

> By end of Week 1: we trust the collector and can detect missing / stale data quickly.

Evidence: `week1-close.ps1` pass, non-zero `ingest_connection_events`, scanner snapshots accumulating, gap report with zero **critical** channels during live scoop.

**Next:** [WEEK2_FEATURE_LAYER.md](./WEEK2_FEATURE_LAYER.md)
