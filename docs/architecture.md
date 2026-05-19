# Architecture

Hyperion is a local-first Rust monorepo split into ingest, trader scoring, signal generation,
paper execution, and API services, with shared crates for configuration, models, schemas, and utilities.

The intended data flow is:

1. `ingest` captures Hyperliquid market and trader activity into PostgreSQL.
2. `trader-engine` scores tracked wallets and computes behavioral profiles and alerts.
3. `signal-engine` builds confidence-ranked consensus signals from scored traders and market context.
4. `execution-engine` replays signals through paper strategies and stores journals and reports.
5. `api` exposes the aggregated read models for dashboards and research tooling.
