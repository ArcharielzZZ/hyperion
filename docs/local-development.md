# Local Development

**Data:** Only code is in git. Postgres dumps, Parquet lakes, and research exports are shared separately — see [DATA_ARTIFACTS.md](./DATA_ARTIFACTS.md).

1. Copy `.env.example` to `.env`.
2. Start infra with `make dev-up` or use the locally installed PostgreSQL/Redis runtimes.
3. Run `make migrate` (or restore a team `pg_dump` into `hyperion` first).
4. Start services with:
   - `make run-ingest`
   - `make run-trader`
   - `make run-signal`
   - `make run-execution`
   - `make run-api`

Health endpoints are exposed on ports `8080` through `8084`.
